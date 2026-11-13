import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, afterEach, before, beforeEach, describe, it } from "node:test";

import {
  context,
  metrics,
  ROOT_CONTEXT,
  SpanKind,
  SpanStatusCode,
  trace,
  type Attributes,
} from "@opentelemetry/api";
import { AsyncLocalStorageContextManager } from "@opentelemetry/context-async-hooks";
import ts from "typescript";
import { MeterProvider, MetricReader, type ResourceMetrics } from "@opentelemetry/sdk-metrics";
import {
  AlwaysOffSampler,
  BasicTracerProvider,
  InMemorySpanExporter,
  ParentBasedSampler,
  SamplingDecision,
  SimpleSpanProcessor,
  type ReadableSpan,
  type SpanExporter,
} from "@opentelemetry/sdk-trace-base";

import { runMigrations } from "../src/lib/db/migrations.ts";
import { createGlossary, createTerm, upsertCredential } from "../src/lib/db/repository.ts";
import { openDatabase, type DatabaseHandle } from "../src/lib/db/sqlite.ts";
import { readTelemetryConfig } from "../src/lib/observability/config.ts";
import { createMetricExporter, createTraceExporter } from "../src/lib/observability/exporters.ts";
import { safeRecord } from "../src/lib/observability/metrics.ts";
import { eventLoopDelaySeconds } from "../src/lib/observability/runtime-metrics.ts";
import {
  RedactingSpanExporter,
  REDACTED,
  sanitizeAttributes,
  sanitizeSpanName,
  sanitizeStacktrace,
  scrubSecrets,
} from "../src/lib/observability/redaction.ts";
import {
  parseTraceparent,
  recordRumEvents,
  validateRumPayload,
} from "../src/lib/observability/rum-ingest.ts";
import { ALLOWED_GMS_ATTRIBUTE_KEYS, normalizePageRoute, SPAN } from "../src/lib/observability/schema.ts";
import { classifyError } from "../src/lib/observability/spans.ts";
import {
  instrumentDatabase,
  sqliteErrorType,
  SQLITE_SPAN_BUDGET_PER_TRACE,
  summarizeSql,
} from "../src/lib/observability/sqlite.ts";
import {
  boundedRoute,
  FORCE_SAMPLE_KEY,
  GmsSampler,
  HttpServerMetricsProcessor,
  RumAwareIdGenerator,
} from "../src/lib/observability/tracing.ts";
import { encryptSecret } from "../src/lib/security/credentials.ts";
import { translateUseCase } from "../src/lib/services/translation.ts";
import { deepLProvider, openAIProvider } from "../src/lib/translation/providers.ts";
import { AppError } from "../src/lib/domain/errors.ts";
import { register as registerInstrumentation } from "../src/instrumentation.ts";

// ---------------------------------------------------------------------------
// Test OpenTelemetry pipeline mirroring production:
// HttpServerMetricsProcessor + RedactingSpanExporter(exporter).
// `raw` captures spans *before* redaction to prove instrumentation itself
// never records sensitive values.
// ---------------------------------------------------------------------------

class TestMetricReader extends MetricReader {
  protected async onForceFlush(): Promise<void> {}
  protected async onShutdown(): Promise<void> {}
}

const raw = new InMemorySpanExporter();
const exported = new InMemorySpanExporter();
const contextManager = new AsyncLocalStorageContextManager();
const tracerProvider = new BasicTracerProvider({
  idGenerator: new RumAwareIdGenerator(),
  sampler: new GmsSampler(new ParentBasedSampler({ root: { shouldSample: () => ({ decision: SamplingDecision.RECORD_AND_SAMPLED }), toString: () => "on" } })),
  spanProcessors: [
    new HttpServerMetricsProcessor(),
    new SimpleSpanProcessor(raw),
    new SimpleSpanProcessor(new RedactingSpanExporter(exported)),
  ],
});
let metricReader = new TestMetricReader();

before(() => {
  context.setGlobalContextManager(contextManager.enable());
  trace.setGlobalTracerProvider(tracerProvider);
});

after(async () => {
  await tracerProvider.shutdown();
  contextManager.disable();
});

function resetMetrics(): void {
  metrics.disable();
  metricReader = new TestMetricReader();
  metrics.setGlobalMeterProvider(new MeterProvider({ readers: [metricReader] }));
}

async function collectMetrics(): Promise<ResourceMetrics> {
  const { resourceMetrics, errors } = await metricReader.collect();
  assert.deepEqual(errors, []);
  return resourceMetrics;
}

type Point = { attributes: Attributes; value: unknown };

function points(resourceMetrics: ResourceMetrics, name: string): Point[] {
  return resourceMetrics.scopeMetrics
    .flatMap((scope) => scope.metrics)
    .filter((metric) => metric.descriptor.name === name)
    .flatMap((metric) => metric.dataPoints as Point[]);
}

function histogramCount(point: Point): number {
  return (point.value as { count: number }).count;
}

function byName(spans: ReadableSpan[], name: string): ReadableSpan {
  const span = spans.find((item) => item.name === name);
  assert.ok(span, `expected span ${name}; got ${spans.map((item) => item.name).join(", ")}`);
  return span;
}

function serialize(spans: ReadableSpan[]): string {
  return JSON.stringify(
    spans.map((span) => ({
      name: span.name,
      attributes: span.attributes,
      events: span.events,
      status: span.status,
      links: span.links,
    })),
  );
}

beforeEach(() => {
  raw.reset();
  exported.reset();
  resetMetrics();
});

// ---------------------------------------------------------------------------
// Database / provider fixtures with canary secrets.
// ---------------------------------------------------------------------------

const CANARIES = {
  originalText: "CANARY-ORIGINAL-本文-7f3a",
  sourceTerm: "CANARY-SOURCE-TERM-91bd",
  targetTerm: "CANARY-TARGET-TERM-0c44",
  deepLKey: "CANARY-DEEPL-KEY-5e21:fx",
  openAIKey: "sk-CANARYOPENAIKEY1234567890",
  intermediate: "CANARY-DEEPL-INTERMEDIATE-a1",
  output: "CANARY-OPENAI-OUTPUT-b2",
  proxyToken: "CANARY-PROXY-TOKEN-c3",
  userEmail: "canary-user@example.test",
};

const handles: DatabaseHandle[] = [];
const originalFetch = globalThis.fetch;
const savedEnv = { ...process.env };

afterEach(() => {
  for (const handle of handles.splice(0)) {
    handle.db.close();
    rmSync(join(handle.path, ".."), { recursive: true, force: true });
  }
  globalThis.fetch = originalFetch;
  for (const key of ["GMS_CREDENTIAL_ENCRYPTION_KEY", "DEEPL_API_URL", "OPENAI_API_URL", "DEEPL_PROXY_TOKEN"]) {
    if (savedEnv[key] === undefined) delete process.env[key];
    else process.env[key] = savedEnv[key];
  }
});

function database() {
  const directory = mkdtempSync(join(tmpdir(), "gmsv5-observability-test-"));
  const handle = openDatabase(join(directory, "gmsv5.sqlite"), "test");
  runMigrations(handle.db);
  handles.push(handle);
  process.env.GMS_CREDENTIAL_ENCRYPTION_KEY = randomBytes(32).toString("base64");
  return handle.db;
}

function seedTranslationFixture() {
  const db = database();
  db.prepare(
    `INSERT INTO users (user_id, email, auth_subject, account_status) VALUES (?, ?, ?, 'ACTIVE')`,
  ).run("owner", CANARIES.userEmail, "subject-owner");
  const glossary = createGlossary(db, "owner", "Glossary", null);
  const term = createTerm(db, "owner", {
    glossaryId: glossary.glossaryId,
    sourceLanguage: "JA",
    targetLanguage: "EN",
    sourceTerm: CANARIES.sourceTerm,
    targetTerm: CANARIES.targetTerm,
    description: null,
  });
  upsertCredential(db, "owner", "DEEPL", encryptSecret(CANARIES.deepLKey));
  upsertCredential(db, "owner", "OPENAI", encryptSecret(CANARIES.openAIKey));
  instrumentDatabase(db);
  return { db, termId: term.termId };
}

function mockProviders(options: { deepLTranslateStatus?: number } = {}) {
  process.env.DEEPL_API_URL = "https://mock.deepl.test/v2/translate";
  process.env.OPENAI_API_URL = "https://mock.openai.test/v1/chat/completions";
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url.endsWith("/glossaries") && method === "POST") {
      return new Response(JSON.stringify({ glossary_id: "glossary-1" }), { status: 200 });
    }
    if (url.endsWith("/translate") && method === "POST") {
      const status = options.deepLTranslateStatus ?? 200;
      return new Response(
        JSON.stringify(status === 200 ? { translations: [{ text: CANARIES.intermediate }] } : { message: CANARIES.originalText }),
        { status },
      );
    }
    if (method === "DELETE") {
      return new Response(null, { status: 204 });
    }
    if (url.includes("mock.openai.test")) {
      return new Response(
        JSON.stringify({ choices: [{ message: { content: CANARIES.output }, finish_reason: "stop" }] }),
        { status: 200 },
      );
    }
    return new Response("not found", { status: 404 });
  };
}

function assertNoCanaries(text: string, where: string): void {
  for (const [name, value] of Object.entries(CANARIES)) {
    assert.ok(!text.includes(value), `${where} leaked ${name}`);
  }
  assert.ok(!text.includes("CANARY"), `${where} leaked a canary fragment`);
}

// ---------------------------------------------------------------------------

describe("telemetry redaction", () => {
  it("strips OAuth codes and search terms from URL attributes and span names", () => {
    const attributes = sanitizeAttributes({
      "http.target": "/api/auth/callback?code=AUTHCODE123&state=STATE456",
      "http.url": "https://gms.example/api/search?q=%E5%BF%83%E4%B8%8D%E5%85%A8",
      "next.route": "/api/auth/callback",
    });
    assert.equal(attributes["http.target"], "/api/auth/callback");
    assert.equal(attributes["http.url"], "https://gms.example/api/search");
    assert.equal(attributes["next.route"], "/api/auth/callback");
    assert.equal(sanitizeSpanName("fetch GET https://x.test/a?code=SECRET"), "fetch GET https://x.test/a");
  });

  it("drops sensitive keys and undeclared gms.* attributes but keeps declared ones", () => {
    const attributes = sanitizeAttributes({
      "http.request.header.authorization": "Bearer abc",
      "http.request.header.cookie": "gms_session=abc",
      "db.query.text": "SELECT * FROM terms WHERE x = 'secret'",
      "db.statement": "SELECT 1",
      "gms.original_text": "secret text",
      "gms.api_key": "secret",
      "deepl.auth_token": "secret",
      "gms.translation.outcome": "success",
      "http.status_code": 200,
    });
    assert.deepEqual(attributes, { "gms.translation.outcome": "success", "http.status_code": 200 });
  });

  it("scrubs secret patterns in free-form strings and caps their length", () => {
    const text = scrubSecrets(
      "Authorization: Bearer eyJabc.def DeepL-Auth-Key abc:fx sk-abcdefghijklmnop gms_session=xyz;",
    );
    assert.ok(!/eyJabc|abc:fx|sk-abcdef|xyz/.test(text), text);
    const long = sanitizeAttributes({ "custom.value": "x".repeat(5000) })["custom.value"] as string;
    assert.ok(long.length < 300);
  });

  it("removes exception messages and keeps stack frames only", () => {
    const stack = sanitizeStacktrace("Error: secret message\n    at foo (/app/a.ts:1:1)\n    at bar (/app/b.ts:2:2)");
    assert.equal(stack, "    at foo (/app/a.ts:1:1)\n    at bar (/app/b.ts:2:2)");
  });

  it("sanitises a leaky Next.js-style server span end to end", () => {
    const span = trace.getTracer("next.js").startSpan("GET /api/auth/callback?code=CANARY-OAUTH", {
      kind: SpanKind.SERVER,
      attributes: { "http.target": "/api/auth/callback?code=CANARY-OAUTH&state=CANARY-STATE" },
    });
    span.recordException(new Error("CANARY-EXCEPTION-MESSAGE"));
    span.setStatus({ code: SpanStatusCode.ERROR, message: "CANARY-STATUS-MESSAGE" });
    span.end();
    const text = serialize(exported.getFinishedSpans());
    assert.ok(!text.includes("CANARY"), text);
    const event = exported.getFinishedSpans()[0].events[0];
    assert.equal(event.attributes?.["exception.message"], REDACTED);
    assert.equal(event.attributes?.["exception.type"], "Error");
  });

  it("fails closed when sanitisation throws", () => {
    const received: ReadableSpan[][] = [];
    const delegate: SpanExporter = {
      export: (spans, done) => {
        received.push(spans);
        done({ code: 0 });
      },
      shutdown: async () => undefined,
    };
    const exporter = new RedactingSpanExporter(delegate);
    const broken = { name: "x", get attributes(): Attributes { throw new Error("boom"); } } as unknown as ReadableSpan;
    let code: number | undefined;
    exporter.export([broken], (result) => {
      code = result.code;
    });
    assert.equal(code, 1);
    assert.equal(received.length, 0);
  });
});

describe("translation use case telemetry", () => {
  it("produces the business span tree without recording text, terms or keys", async () => {
    const { db, termId } = seedTranslationFixture();
    mockProviders();

    const result = await trace.getTracer("test").startActiveSpan("POST /api/translate", { kind: SpanKind.SERVER }, async (root) => {
      try {
        return await translateUseCase(db, "owner", {
          originalText: CANARIES.originalText,
          sourceLanguage: "JA",
          targetLanguage: "EN",
          selectedTermIds: [termId],
          consentConfirmed: true,
        });
      } finally {
        root.end();
      }
    });
    assert.equal(result.translatedText, CANARIES.output);

    const spans = exported.getFinishedSpans();
    const request = byName(spans, SPAN.translationRequest);
    const parentOf = (span: ReadableSpan) => span.parentSpanContext?.spanId;
    for (const child of [
      SPAN.translationValidate,
      SPAN.sqliteReadTranslationTerms,
      SPAN.translationResolveTerms,
      SPAN.credentialsLoad,
      SPAN.deeplTranslate,
      SPAN.openaiPostEdit,
    ]) {
      assert.equal(parentOf(byName(spans, child)), request.spanContext().spanId, `${child} parent`);
    }
    const termsQuery = spans.find(
      (span) => span.name === "SELECT terms" && parentOf(span) === byName(spans, SPAN.sqliteReadTranslationTerms).spanContext().spanId,
    );
    assert.ok(termsQuery, "statement span under gms.sqlite.read_translation_terms");
    assert.equal(termsQuery.attributes["db.system.name"], "sqlite");
    assert.equal(termsQuery.attributes["db.query.text"], undefined);
    assert.equal(request.attributes["gms.translation.outcome"], "success");
    assert.equal(request.attributes["gms.translation.selected_term_count"], 1);
    assert.equal(request.attributes["gms.translation.source_text_length"], CANARIES.originalText.length);
    // The SDK derives span start times from `Date.now()` (1 ms resolution)
    // and end times as start + a sub-ms `performance.now()` duration, so
    // timestamps of *different* spans are only comparable to within 1 ms.
    // Comparing at ns resolution made this assertion fail intermittently.
    const toNanos = ([seconds, nanos]: readonly [number, number]) => BigInt(seconds) * BigInt(1e9) + BigInt(nanos);
    const deeplEnd = toNanos(byName(spans, SPAN.deeplTranslate).endTime);
    const openaiStart = toNanos(byName(spans, SPAN.openaiPostEdit).startTime);
    assert.ok(deeplEnd <= openaiStart + BigInt(1e6), "DeepL completes before OpenAI starts (within Date.now() resolution)");

    assertNoCanaries(serialize(raw.getFinishedSpans()), "raw spans");
    assertNoCanaries(serialize(spans), "exported spans");

    const collected = await collectMetrics();
    assertNoCanaries(JSON.stringify(collected.scopeMetrics), "metrics");
    const translation = points(collected, "gms.translation.duration");
    assert.equal(translation.length, 1);
    assert.equal(translation[0].attributes["gms.translation.outcome"], "success");
    const external = points(collected, "gms.external.request.duration").map(
      (point) => `${point.attributes["gms.provider"]}:${point.attributes["gms.external.operation"]}:${point.attributes["http.response.status_code"]}`,
    );
    assert.deepEqual(new Set(external), new Set([
      "deepl:glossary_create:200",
      "deepl:translate:200",
      "deepl:glossary_delete:204",
      "openai:post_edit:200",
    ]));
    const db_points = points(collected, "db.client.operation.duration");
    assert.ok(db_points.some((point) => point.attributes["db.collection.name"] === "terms"));
    for (const point of [...translation, ...db_points, ...points(collected, "gms.external.request.duration")]) {
      for (const key of Object.keys(point.attributes)) {
        assert.ok(
          !key.startsWith("gms.") || ALLOWED_GMS_ATTRIBUTE_KEYS.has(key),
          `undeclared metric attribute ${key}`,
        );
        assert.ok(!/user|glossary_id|term_id|trace|url/i.test(key), `high-cardinality metric attribute ${key}`);
      }
    }
  });

  it("classifies a DeepL failure as a dependency error without leaking the response", async () => {
    const { db, termId } = seedTranslationFixture();
    mockProviders({ deepLTranslateStatus: 429 });
    await assert.rejects(
      translateUseCase(db, "owner", {
        originalText: CANARIES.originalText,
        sourceLanguage: "JA",
        targetLanguage: "EN",
        selectedTermIds: [termId],
        consentConfirmed: true,
      }),
      /DeepL translation failed/,
    );
    const spans = exported.getFinishedSpans();
    const deepl = byName(spans, SPAN.deeplTranslate);
    assert.equal(deepl.status.code, SpanStatusCode.ERROR);
    assert.equal(deepl.attributes["error.type"], "DEEPL_FAILED");
    assert.equal(byName(spans, SPAN.translationRequest).attributes["gms.translation.outcome"], "dependency_error");
    assert.equal(spans.some((span) => span.name === SPAN.openaiPostEdit), false);
    assertNoCanaries(serialize(spans), "exported spans");

    const collected = await collectMetrics();
    assert.ok(
      points(collected, "gms.external.request.duration").some(
        (point) => point.attributes["gms.external.operation"] === "translate" && point.attributes["error.type"] === "429",
      ),
    );
    assert.equal(points(collected, "gms.translation.duration")[0].attributes["error.type"], "DEEPL_FAILED");
  });

  it("treats conflicts and validation errors as non-error spans with client outcomes", async () => {
    const db = database();
    await assert.rejects(
      translateUseCase(db, "nobody", {
        originalText: "x",
        sourceLanguage: "JA",
        targetLanguage: "EN",
        selectedTermIds: [],
        consentConfirmed: false,
      }),
      /consent/,
    );
    const request = byName(exported.getFinishedSpans(), SPAN.translationRequest);
    assert.equal(request.status.code, SpanStatusCode.UNSET);
    assert.equal(request.attributes["gms.translation.outcome"], "client_error");
    assert.equal(request.attributes["gms.error.code"], "TRANSLATION_CONSENT_REQUIRED");
  });

  it("keeps working when the metrics pipeline throws", () => {
    metrics.disable();
    metrics.setGlobalMeterProvider({
      getMeter: () => {
        throw new Error("meter failure");
      },
    });
    assert.doesNotThrow(() => safeRecord(({ translationDuration }) => translationDuration.record(1)));
    resetMetrics();
  });

  it("keeps provider HTTP contracts unchanged", async () => {
    mockProviders();
    assert.equal(
      await deepLProvider.translate({ originalText: "a", sourceLanguage: "JA", targetLanguage: "EN", terms: [] }, "k"),
      CANARIES.intermediate,
    );
    assert.equal(
      await openAIProvider.translate({ originalText: "a", sourceLanguage: "JA", targetLanguage: "EN", terms: [] }, "k"),
      CANARIES.output,
    );
  });
});

describe("SQLite instrumentation", () => {
  it("summarises SQL without data", () => {
    assert.deepEqual(summarizeSql("SELECT t.* FROM terms t WHERE term_id IN (?, ?)"), {
      operation: "SELECT",
      collection: "terms",
      summary: "SELECT terms",
    });
    assert.equal(summarizeSql("  INSERT INTO api_credentials (a) VALUES (?)").summary, "INSERT api_credentials");
    assert.equal(summarizeSql("UPDATE OR IGNORE users SET x = ?").summary, "UPDATE users");
    assert.equal(summarizeSql("PRAGMA foreign_keys = ON").summary, "PRAGMA foreign_keys");
    assert.equal(summarizeSql("BEGIN IMMEDIATE").summary, "BEGIN");
    assert.equal(summarizeSql("SELECT 'secret' AS x").summary, "SELECT");
    assert.equal(summarizeSql("garbage 'secret'").summary, "OTHER");
  });

  it("maps SQLite errors to bounded error types and keeps throwing the original error", () => {
    const db = database();
    instrumentDatabase(db);
    db.exec("CREATE TABLE sample (id TEXT PRIMARY KEY)");
    db.prepare("INSERT INTO sample (id) VALUES (?)").run("a");
    let caught: unknown;
    trace.getTracer("test").startActiveSpan("parent", (parent) => {
      try {
        db.prepare("INSERT INTO sample (id) VALUES (?)").run("a");
      } catch (error) {
        caught = error;
      } finally {
        parent.end();
      }
    });
    assert.match(String((caught as Error).message), /UNIQUE constraint failed/);
    assert.equal(sqliteErrorType(caught).errorType, "SQLITE_CONSTRAINT");
    const insert = exported.getFinishedSpans().find((span) => span.name === "INSERT sample");
    assert.equal(insert?.attributes["error.type"], "SQLITE_CONSTRAINT");
    assert.equal(insert?.status.code, SpanStatusCode.ERROR);
  });

  it("does not start root spans and caps statement spans per trace", async () => {
    const db = database();
    instrumentDatabase(db);
    db.prepare("SELECT 1 AS one").get();
    assert.equal(exported.getFinishedSpans().length, 0, "no parent → no span");

    trace.getTracer("test").startActiveSpan("bulk import", (parent) => {
      const statement = db.prepare("SELECT 1 AS one");
      for (let index = 0; index < SQLITE_SPAN_BUDGET_PER_TRACE + 50; index += 1) {
        statement.get();
      }
      parent.end();
    });
    const spans = exported.getFinishedSpans();
    assert.equal(spans.filter((span) => span.name === "SELECT").length, SQLITE_SPAN_BUDGET_PER_TRACE);
    assert.equal(byName(spans, "bulk import").attributes["gms.sqlite.spans_suppressed"], true);
    const collected = await collectMetrics();
    const selectPoint = points(collected, "db.client.operation.duration").find(
      (point) => point.attributes["db.operation.name"] === "SELECT" && point.attributes["db.collection.name"] === undefined,
    );
    assert.equal(selectPoint && histogramCount(selectPoint), SQLITE_SPAN_BUDGET_PER_TRACE + 51, "metrics are not capped");
  });

  it("is idempotent and preserves results", () => {
    const db = database();
    instrumentDatabase(db);
    instrumentDatabase(db);
    assert.deepEqual({ ...db.prepare("SELECT 1 AS one").get() }, { one: 1 });
    assert.deepEqual(db.prepare("SELECT 2 AS two").all().map((row) => ({ ...row })), [{ two: 2 }]);
  });
});

describe("sampling, IDs and HTTP metrics", () => {
  it("records unsampled server spans for metrics and honours forced sampling", () => {
    const sampler = new GmsSampler(new AlwaysOffSampler());
    assert.equal(sampler.shouldSample(ROOT_CONTEXT, "a".repeat(32), "GET", SpanKind.SERVER, {}, []).decision, SamplingDecision.RECORD);
    assert.equal(sampler.shouldSample(ROOT_CONTEXT, "a".repeat(32), "x", SpanKind.INTERNAL, {}, []).decision, SamplingDecision.NOT_RECORD);
    assert.equal(
      sampler.shouldSample(ROOT_CONTEXT.setValue(FORCE_SAMPLE_KEY, true), "a".repeat(32), "x", SpanKind.INTERNAL, {}, []).decision,
      SamplingDecision.RECORD_AND_SAMPLED,
    );
    const always = new GmsSampler({ shouldSample: () => ({ decision: SamplingDecision.RECORD_AND_SAMPLED }), toString: () => "on" });
    assert.equal(
      always.shouldSample(ROOT_CONTEXT, "a".repeat(32), "POST", SpanKind.SERVER, { "http.target": "/api/telemetry/rum" }, []).decision,
      SamplingDecision.RECORD,
      "beacon traces are not exported",
    );
  });

  it("derives http.server.request.duration with bounded routes only", async () => {
    const processor = new HttpServerMetricsProcessor();
    const span = {
      kind: SpanKind.SERVER,
      duration: [0, 250_000_000],
      attributes: {
        "next.span_type": "BaseServer.handleRequest",
        "http.method": "GET",
        "http.status_code": 503,
        "http.target": "/api/search?q=secret",
        "next.route": "/api/search",
      },
    } as unknown as ReadableSpan;
    processor.onEnd(span);
    processor.onEnd({ ...span, attributes: { ...span.attributes, "next.route": "/api/glossaries/123?x=1" } } as unknown as ReadableSpan);
    const recorded = points(await collectMetrics(), "http.server.request.duration");
    assert.deepEqual(recorded.map((point) => point.attributes["http.route"]).sort(), ["/api/search", "unmatched"]);
    assert.equal(recorded[0].attributes["error.type"], "503");
    assert.equal(boundedRoute({ "http.route": "/api/glossaries/[glossaryId]/terms" }), "/api/glossaries/[glossaryId]/terms");
  });
});

describe("Browser RUM ingestion", () => {
  const traceparent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01";

  it("validates the schema strictly", () => {
    const result = validateRumPayload({
      v: 1,
      events: [
        { type: "action", name: "translation", outcome: "success", durationMs: 1234, endedAgoMs: 10, route: "/translation", httpStatus: 200, traceparent },
        { type: "vital", name: "LCP", value: 1800, rating: "good", route: "/glossary" },
        { type: "action", name: "translation", outcome: "success", durationMs: 1, endedAgoMs: 1, route: "/translation", text: "secret" },
        { type: "action", name: "delete_everything", outcome: "success", durationMs: 1, endedAgoMs: 1, route: "/" },
        { type: "action", name: "select_all", outcome: "conflict", durationMs: 1, endedAgoMs: 1, route: "/" },
        { type: "action", name: "select_all", outcome: "success", durationMs: -1, endedAgoMs: 1, route: "/" },
        { type: "vital", name: "LCP", value: 1, rating: "good", route: "/api/search?q=secret" },
        { type: "action", name: "translation", outcome: "error", durationMs: 1, endedAgoMs: 1, route: "/", traceparent: "00-zz" },
      ],
    });
    assert.equal(result.events.length, 2);
    assert.deepEqual(result.rejected, {
      unknown_field: 1,
      invalid_name: 1,
      invalid_outcome: 1,
      invalid_number: 1,
      invalid_route: 1,
      invalid_traceparent: 1,
    });
    assert.throws(() => validateRumPayload({ v: 2, events: [] }));
    assert.throws(() => validateRumPayload([]));
    const many = validateRumPayload({
      v: 1,
      events: Array.from({ length: 25 }, () => ({ type: "vital", name: "CLS", value: 0.01, rating: "good", route: "/" })),
    });
    assert.equal(many.events.length, 20);
    assert.equal(many.rejected.too_many_events, 5);
  });

  it("re-creates the browser span with the browser's trace and span IDs", async () => {
    const receivedAt = Date.now();
    recordRumEvents(
      validateRumPayload({
        v: 1,
        events: [
          { type: "action", name: "translation", outcome: "success", durationMs: 1500, endedAgoMs: 100, route: "/translation", httpStatus: 200, traceparent },
          { type: "action", name: "select_all", outcome: "success", durationMs: 50, endedAgoMs: 1, route: "/translation", traceparent: traceparent.replace(/-01$/, "-00") },
        ],
      }),
      receivedAt,
    );
    const spans = exported.getFinishedSpans();
    assert.equal(spans.length, 1, "unsampled browser actions produce metrics only");
    const span = spans[0];
    assert.equal(span.name, SPAN.rumTranslation);
    assert.equal(span.spanContext().traceId, "0af7651916cd43dd8448eb211c80319c");
    assert.equal(span.spanContext().spanId, "b7ad6b7169203331");
    assert.equal(span.parentSpanContext, undefined);
    assert.equal(span.attributes["gms.telemetry.source"], "browser");
    const durationMs = span.duration[0] * 1000 + span.duration[1] / 1e6;
    assert.ok(Math.abs(durationMs - 1500) < 1, `duration ${durationMs}`);

    // A server span continuing the browser trace becomes its child.
    const parent = trace.setSpanContext(ROOT_CONTEXT, {
      traceId: "0af7651916cd43dd8448eb211c80319c",
      spanId: "b7ad6b7169203331",
      traceFlags: 1,
      isRemote: true,
    });
    trace.getTracer("next.js").startSpan("POST /api/translate", { kind: SpanKind.SERVER }, parent).end();
    const server = exported.getFinishedSpans()[1];
    assert.equal(server.parentSpanContext?.spanId, "b7ad6b7169203331");
    assert.notEqual(server.spanContext().spanId, "b7ad6b7169203331");

    const collected = await collectMetrics();
    const actions = points(collected, "gms.rum.user_action.duration");
    assert.equal(actions.length, 2);
    assert.ok(actions.every((point) => Object.keys(point.attributes).every((key) => ALLOWED_GMS_ATTRIBUTE_KEYS.has(key))));
  });

  it("shares forced span IDs across duplicated module instances (separate Next.js chunks)", async () => {
    const copy = (await import(`../src/lib/observability/tracing.ts?chunk=${Date.now()}`)) as typeof import("../src/lib/observability/tracing.ts");
    assert.notEqual(copy.RumAwareIdGenerator, RumAwareIdGenerator, "a distinct module instance");
    const generator = new RumAwareIdGenerator();
    const ids = copy.withForcedSpanIds({ traceId: "1".repeat(32), spanId: "2".repeat(16) }, () => [
      generator.generateTraceId(),
      generator.generateSpanId(),
      generator.generateSpanId(),
    ]);
    assert.equal(ids[0], "1".repeat(32));
    assert.equal(ids[1], "2".repeat(16));
    assert.notEqual(ids[2], "2".repeat(16), "forced span ID is used once");
  });

  it("parses W3C traceparent and normalises page routes", () => {
    assert.deepEqual(parseTraceparent(traceparent), {
      traceId: "0af7651916cd43dd8448eb211c80319c",
      spanId: "b7ad6b7169203331",
      sampled: true,
    });
    assert.equal(parseTraceparent(`00-${"0".repeat(32)}-b7ad6b7169203331-01`), null);
    assert.equal(normalizePageRoute("/translation/"), "/translation");
    assert.equal(normalizePageRoute("/glossary/secret-id"), "other");
  });
});

describe("telemetry configuration", () => {
  it("is opt-in and derives OTLP endpoints from the environment", () => {
    assert.equal(readTelemetryConfig({}).enabled, false);
    const config = readTelemetryConfig({
      GMS_OTEL_ENABLED: "true",
      OTEL_EXPORTER_OTLP_ENDPOINT: "http://collector:4318/",
      OTEL_EXPORTER_OTLP_HEADERS: "authorization=Bearer%20abc,x-tenant=gms,bad header=1",
      OTEL_TRACES_SAMPLER: "parentbased_traceidratio",
      OTEL_TRACES_SAMPLER_ARG: "0.25",
      GMS_RELEASE_VERSION: "abc123",
    });
    assert.equal(config.enabled, true);
    assert.equal(config.tracesEndpoint, "http://collector:4318/v1/traces");
    assert.equal(config.metricsEndpoint, "http://collector:4318/v1/metrics");
    assert.equal(config.sampler, "parentbased_traceidratio");
    assert.equal(config.samplerRatio, 0.25);
    assert.equal(config.serviceVersion, "abc123");
    assert.equal(config.rumEnabled, false);
    assert.equal(readTelemetryConfig({ OTEL_TRACES_SAMPLER: "bogus" }).sampler, "parentbased_always_on");
  });

  it("reports event loop delay net of the sampling resolution", () => {
    assert.equal(eventLoopDelaySeconds(19.5e6, 20), 0);
    assert.equal(eventLoopDelaySeconds(515e6, 20), 0.495);
    assert.equal(eventLoopDelaySeconds(Number.NaN, 20), 0);
  });

  it("classifies errors without messages", () => {
    assert.deepEqual(classifyError(new AppError(409, "TERM_CONFLICT", "secret")), { errorClass: "client", errorType: "TERM_CONFLICT" });
    assert.deepEqual(classifyError(new AppError(502, "DEEPL_FAILED", "secret")), { errorClass: "dependency", errorType: "DEEPL_FAILED" });
    assert.deepEqual(classifyError(new TypeError("secret")), { errorClass: "server", errorType: "TypeError" });
  });
});

describe("instrumentation runtime guard", () => {
  const instrumentationPath = new URL("../src/instrumentation.ts", import.meta.url);

  function isNodeRuntimeCheck(expression: ts.Expression): boolean {
    return (
      ts.isBinaryExpression(expression) &&
      expression.operatorToken.kind === ts.SyntaxKind.EqualsEqualsEqualsToken &&
      expression.left.getText() === "process.env.NEXT_RUNTIME" &&
      ts.isStringLiteral(expression.right) &&
      expression.right.text === "nodejs"
    );
  }

  // Regression: `next dev --webpack` failed with `UnhandledSchemeError: node:fs`
  // because the Edge compile parsed `import("./lib/observability/node-sdk")`
  // placed after an early `return`. Webpack only removes the untaken branch
  // of an `if` whose condition it can fold, so every dynamic import must sit
  // inside `if (process.env.NEXT_RUNTIME === "nodejs") { ... }`.
  it("keeps Node.js-only imports inside the NEXT_RUNTIME === \"nodejs\" branch", () => {
    const source = ts.createSourceFile(
      "instrumentation.ts",
      readFileSync(instrumentationPath, "utf8"),
      ts.ScriptTarget.Latest,
      true,
    );

    const staticImports = source.statements.filter(ts.isImportDeclaration).filter((node) => !node.importClause?.isTypeOnly);
    assert.deepEqual(staticImports.map((node) => node.getText()), [], "instrumentation.ts must not statically import runtime code");

    const dynamicImports: ts.CallExpression[] = [];
    const visit = (node: ts.Node) => {
      if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) {
        dynamicImports.push(node);
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
    assert.ok(dynamicImports.length > 0, "expected node-sdk to be loaded via dynamic import");

    for (const call of dynamicImports) {
      let guarded = false;
      let child: ts.Node = call;
      for (let parent = call.parent; parent; child = parent, parent = parent.parent) {
        if (ts.isIfStatement(parent) && parent.thenStatement === child && isNodeRuntimeCheck(parent.expression)) {
          guarded = true;
          break;
        }
      }
      assert.ok(guarded, `${call.getText()} must be inside if (process.env.NEXT_RUNTIME === "nodejs")`);
    }
  });

  describe("register() disable conditions", () => {
    const saved = { runtime: process.env.NEXT_RUNTIME, enabled: process.env.GMS_OTEL_ENABLED };
    let errors: unknown[][] = [];
    const originalError = console.error;

    beforeEach(() => {
      errors = [];
      console.error = (...args: unknown[]) => {
        errors.push(args);
      };
    });

    afterEach(() => {
      console.error = originalError;
      for (const [key, value] of [["NEXT_RUNTIME", saved.runtime], ["GMS_OTEL_ENABLED", saved.enabled]] as const) {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
      }
    });

    // Under the test runner the extensionless `node-sdk` specifier cannot be
    // resolved, so any attempt to load the SDK surfaces through the guarded
    // console.error path. No error therefore means no import was attempted.
    for (const [label, runtime, enabled] of [
      ["Edge runtime with telemetry enabled", "edge", "true"],
      ["unknown runtime with telemetry enabled", undefined, "true"],
      ["Node.js runtime with telemetry unset", "nodejs", undefined],
      ["Node.js runtime with telemetry not exactly \"true\"", "nodejs", "TRUE"],
    ] as const) {
      it(`does not load the SDK for ${label}`, async () => {
        if (runtime === undefined) delete process.env.NEXT_RUNTIME;
        else process.env.NEXT_RUNTIME = runtime;
        if (enabled === undefined) delete process.env.GMS_OTEL_ENABLED;
        else process.env.GMS_OTEL_ENABLED = enabled;

        await registerInstrumentation();
        assert.deepEqual(errors, []);
      });
    }
  });
});

describe("OTLP exporters", () => {
  // Header, TLS (OTEL_EXPORTER_OTLP_CERTIFICATE / CLIENT_*) and compression
  // variables are read by the exporters themselves. Both signals must use the
  // same Node.js HTTP transport so that they honour these identically; the
  // previous fetch-based trace exporter ignored the certificate variables.
  const ENV_KEYS = ["OTEL_EXPORTER_OTLP_HEADERS", "OTEL_EXPORTER_OTLP_TRACES_HEADERS", "OTEL_EXPORTER_OTLP_METRICS_HEADERS"] as const;
  const SUCCESS = 0; // ExportResultCode.SUCCESS
  const FAILED = 1; // ExportResultCode.FAILED
  type Received = { path?: string; contentType?: string; authorization?: string; tenant?: string; body: string };
  let server: Server;
  let endpoint = "";
  let received: Received[] = [];
  let status = 200;
  const savedEnv = new Map<string, string | undefined>();

  before(async () => {
    server = createServer(async (request, response) => {
      const chunks: Buffer[] = [];
      for await (const chunk of request) chunks.push(chunk as Buffer);
      received.push({
        path: request.url,
        contentType: request.headers["content-type"],
        authorization: request.headers.authorization,
        tenant: request.headers["x-gms-tenant"] as string | undefined,
        body: Buffer.concat(chunks).toString("latin1"),
      });
      const isTraces = request.url === "/v1/traces";
      response.writeHead(status, { "content-type": isTraces ? "application/x-protobuf" : "application/json" });
      response.end(status === 200 && !isTraces ? "{}" : undefined);
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    endpoint = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });

  after(() => {
    server.close();
  });

  beforeEach(() => {
    received = [];
    status = 200;
    for (const key of ENV_KEYS) savedEnv.set(key, process.env[key]);
  });

  afterEach(() => {
    for (const key of ENV_KEYS) {
      const value = savedEnv.get(key);
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  });

  const exporterConfig = () => ({
    tracesEndpoint: `${endpoint}/v1/traces`,
    metricsEndpoint: `${endpoint}/v1/metrics`,
    exportTimeoutMillis: 5000,
  });

  function finishedSpans(): ReadableSpan[] {
    const memory = new InMemorySpanExporter();
    const provider = new BasicTracerProvider({ spanProcessors: [new SimpleSpanProcessor(memory)] });
    provider.getTracer("exporter-contract").startSpan("gms.exporter.contract").end();
    return memory.getFinishedSpans();
  }

  async function collectedMetrics(): Promise<ResourceMetrics> {
    const reader = new TestMetricReader();
    new MeterProvider({ readers: [reader] }).getMeter("exporter-contract").createCounter("gms.exporter.contract").add(1);
    return (await reader.collect()).resourceMetrics;
  }

  it("sends protobuf traces and JSON metrics with spec-compliant OTLP header precedence", async () => {
    process.env.OTEL_EXPORTER_OTLP_HEADERS = "authorization=Bearer%20generic-token,x-gms-tenant=gms";
    process.env.OTEL_EXPORTER_OTLP_TRACES_HEADERS = "authorization=Bearer%20traces-token";

    const traceExporter = createTraceExporter(exporterConfig());
    const metricExporter = createMetricExporter(exporterConfig());
    try {
      const traceResult = await new Promise<{ code: number }>((resolve) => traceExporter.export(finishedSpans(), resolve));
      const resourceMetrics = await collectedMetrics();
      const metricResult = await new Promise<{ code: number }>((resolve) => metricExporter.export(resourceMetrics, resolve));
      assert.equal(traceResult.code, SUCCESS);
      assert.equal(metricResult.code, SUCCESS);
    } finally {
      await traceExporter.shutdown();
      await metricExporter.shutdown();
    }

    const traces = received.find((request) => request.path === "/v1/traces");
    const metricsRequest = received.find((request) => request.path === "/v1/metrics");
    assert.ok(traces && metricsRequest, "both signals exported");
    assert.equal(traces.contentType, "application/x-protobuf");
    assert.equal(traces.authorization, "Bearer traces-token", "signal-specific header wins");
    assert.equal(traces.tenant, "gms", "generic headers are merged");
    assert.ok(traces.body.includes("gms.exporter.contract"));
    assert.equal(metricsRequest.contentType, "application/json");
    assert.equal(metricsRequest.authorization, "Bearer generic-token");
    assert.equal(metricsRequest.tenant, "gms");
    assert.ok(metricsRequest.body.includes("gms.exporter.contract"));
  });

  it("reports a rejected export (401) as a failed result without throwing", async () => {
    status = 401;
    process.env.OTEL_EXPORTER_OTLP_HEADERS = "authorization=Bearer%20wrong-token";
    const traceExporter = createTraceExporter(exporterConfig());
    const metricExporter = createMetricExporter(exporterConfig());
    try {
      const traceResult = await new Promise<{ code: number }>((resolve) => traceExporter.export(finishedSpans(), resolve));
      const resourceMetrics = await collectedMetrics();
      const metricResult = await new Promise<{ code: number }>((resolve) => metricExporter.export(resourceMetrics, resolve));
      assert.equal(traceResult.code, FAILED);
      assert.equal(metricResult.code, FAILED);
    } finally {
      await traceExporter.shutdown();
      await metricExporter.shutdown();
    }
    assert.equal(received.length, 2, "401 is not retried");
  });
});
