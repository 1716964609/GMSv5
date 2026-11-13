/**
 * Trace pipeline components registered by `node-sdk.ts`.
 *
 * - GmsSampler: wraps the configured sampler. Server spans that the sampler
 *   drops are still *recorded* (not exported) so that HTTP metrics derived
 *   from them are independent of the trace sampling rate (OD-07). Also lets
 *   the RUM ingestion force-sample a browser span whose `traceparent` the
 *   browser marked sampled, keeping it consistent with ParentBased children.
 * - RumAwareIdGenerator: lets RUM ingestion re-create the browser's user
 *   action span with the exact trace / span IDs the browser sent in
 *   `traceparent`, so the server spans become its children (As-To-Be §6.2).
 * - HttpServerMetricsProcessor: derives `http.server.request.duration` from
 *   the Next.js root server span with bounded attributes only.
 */

import { AsyncLocalStorage } from "node:async_hooks";

import {
  createContextKey,
  SpanKind,
  type Attributes,
  type Context,
  type Link,
} from "@opentelemetry/api";
import {
  AlwaysOffSampler,
  AlwaysOnSampler,
  ParentBasedSampler,
  RandomIdGenerator,
  SamplingDecision,
  TraceIdRatioBasedSampler,
  type IdGenerator,
  type ReadableSpan,
  type Sampler,
  type SamplingResult,
  type SpanProcessor,
} from "@opentelemetry/sdk-trace-base";

import type { SamplerName } from "./config.ts";
import { safeRecord } from "./metrics.ts";

export const FORCE_SAMPLE_KEY = createContextKey("gms.force_sample");

export function createBaseSampler(name: SamplerName, ratio: number): Sampler {
  switch (name) {
    case "always_on":
      return new AlwaysOnSampler();
    case "always_off":
      return new AlwaysOffSampler();
    case "traceidratio":
      return new TraceIdRatioBasedSampler(ratio);
    case "parentbased_always_off":
      return new ParentBasedSampler({ root: new AlwaysOffSampler() });
    case "parentbased_traceidratio":
      return new ParentBasedSampler({ root: new TraceIdRatioBasedSampler(ratio) });
    case "parentbased_always_on":
    default:
      return new ParentBasedSampler({ root: new AlwaysOnSampler() });
  }
}

const TELEMETRY_INGESTION_PATH = "/api/telemetry/";

function isTelemetryIngestionRequest(attributes: Attributes): boolean {
  const target = attributes["http.target"] ?? attributes["url.path"];
  return typeof target === "string" && target.startsWith(TELEMETRY_INGESTION_PATH);
}

export class GmsSampler implements Sampler {
  private readonly delegate: Sampler;

  constructor(delegate: Sampler) {
    this.delegate = delegate;
  }

  shouldSample(
    context: Context,
    traceId: string,
    spanName: string,
    spanKind: SpanKind,
    attributes: Attributes,
    links: Link[],
  ): SamplingResult {
    if (context.getValue(FORCE_SAMPLE_KEY) === true) {
      return { decision: SamplingDecision.RECORD_AND_SAMPLED };
    }
    if (spanKind === SpanKind.SERVER && isTelemetryIngestionRequest(attributes)) {
      // RUM beacons: keep HTTP metrics, do not export a trace per beacon.
      return { decision: SamplingDecision.RECORD };
    }
    const result = this.delegate.shouldSample(context, traceId, spanName, spanKind, attributes, links);
    if (result.decision === SamplingDecision.NOT_RECORD && spanKind === SpanKind.SERVER) {
      return { ...result, decision: SamplingDecision.RECORD };
    }
    return result;
  }

  toString(): string {
    return `GmsSampler{${this.delegate.toString()}}`;
  }
}

type ForcedIds = { traceId: string; spanId: string };

/**
 * Process-wide singleton. Next.js bundles `instrumentation.ts` (which owns
 * the IdGenerator) and each route handler (which calls `withForcedSpanIds`)
 * into separate webpack chunks, each with its own copy of this module; a
 * module-level AsyncLocalStorage would not be shared between them.
 * (Found during local standalone verification, 2026-11-13.)
 */
const FORCED_IDS_KEY = Symbol.for("gms.observability.forced_span_ids");
const forcedIds: AsyncLocalStorage<ForcedIds> = ((globalThis as Record<symbol, unknown>)[FORCED_IDS_KEY] ??=
  new AsyncLocalStorage<ForcedIds>()) as AsyncLocalStorage<ForcedIds>;

export class RumAwareIdGenerator implements IdGenerator {
  private readonly random = new RandomIdGenerator();

  generateTraceId(): string {
    return forcedIds.getStore()?.traceId ?? this.random.generateTraceId();
  }

  generateSpanId(): string {
    const store = forcedIds.getStore();
    if (store) {
      // Use the forced span ID exactly once (for the RUM root span).
      const { spanId } = store;
      store.spanId = "";
      if (spanId) {
        return spanId;
      }
    }
    return this.random.generateSpanId();
  }
}

/** Run `fn` so that the next root span started inside it uses `ids`. */
export function withForcedSpanIds<T>(ids: ForcedIds, fn: () => T): T {
  return forcedIds.run({ ...ids }, fn);
}

const NEXT_ROOT_SPAN_TYPE = "BaseServer.handleRequest";
const ROUTE_PATTERN = /^\/[A-Za-z0-9_\-/[\].()@]*$/;

function hrSeconds(duration: [number, number]): number {
  return duration[0] + duration[1] / 1e9;
}

/** Bounded `http.route`: never a raw request target (cardinality §9.2). */
export function boundedRoute(attributes: Attributes): string {
  const route = attributes["http.route"] ?? attributes["next.route"];
  return typeof route === "string" && route.length <= 128 && ROUTE_PATTERN.test(route) ? route : "unmatched";
}

export class HttpServerMetricsProcessor implements SpanProcessor {
  onStart(): void {}

  onEnd(span: ReadableSpan): void {
    if (span.kind !== SpanKind.SERVER || span.attributes["next.span_type"] !== NEXT_ROOT_SPAN_TYPE) {
      return;
    }
    const method = span.attributes["http.method"] ?? span.attributes["http.request.method"];
    const status = span.attributes["http.status_code"] ?? span.attributes["http.response.status_code"];
    const statusCode = typeof status === "number" ? status : Number(status);
    const seconds = hrSeconds(span.duration);
    safeRecord(({ httpServerDuration }) =>
      httpServerDuration.record(seconds, {
        "http.request.method": typeof method === "string" && /^[A-Z]{3,7}$/.test(method) ? method : "_OTHER",
        "http.route": boundedRoute(span.attributes),
        ...(Number.isInteger(statusCode) ? { "http.response.status_code": statusCode } : {}),
        ...(Number.isInteger(statusCode) && statusCode >= 500 ? { "error.type": String(statusCode) } : {}),
      }),
    );
  }

  forceFlush(): Promise<void> {
    return Promise.resolve();
  }

  shutdown(): Promise<void> {
    return Promise.resolve();
  }
}
