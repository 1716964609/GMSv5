/**
 * Telemetry redaction — the last line of defence before spans leave the
 * process. Architecture As-To-Be §9.1 forbids translation text, API keys,
 * OAuth tokens, cookies, session secrets, encryption keys and SQL parameters
 * in any span attribute, event, status message or span name.
 *
 * Known leak paths this layer closes (verified against Next.js 16.4 and
 * @vercel/otel 2.3 sources):
 * - Next.js root span `http.target` is `req.url` *including the query string*
 *   (`/api/auth/callback?code=…&state=…`, `/api/search?q=<user term>`).
 * - Next.js records `exception` events and copies `error.message` into the
 *   span status message.
 * - @vercel/otel fetch/http instrumentation puts the full URL into
 *   `http.url` and into the span name.
 *
 * Policy: allowlist for GMS-owned `gms.*` keys, denylist for well-known
 * sensitive keys, query-string stripping for URL-valued keys, secret pattern
 * scrubbing and a length cap for every remaining string. If sanitisation
 * itself fails the span is dropped (fail closed).
 */

import type { Attributes, AttributeValue, Link, SpanStatus } from "@opentelemetry/api";
import type { ReadableSpan, SpanExporter, TimedEvent } from "@opentelemetry/sdk-trace-base";

import { ALLOWED_GMS_ATTRIBUTE_KEYS } from "./schema.ts";

export const REDACTED = "[REDACTED]";
const MAX_STRING_LENGTH = 256;
const MAX_STACKTRACE_LENGTH = 4096;
const MAX_ARRAY_LENGTH = 32;

const DROPPED_KEY_PATTERNS: readonly RegExp[] = [
  /^http\.(request|response)\.header\./,
  /^http\.(request|response)\.body/,
  /^db\.(statement|query\.text|query\.parameter\.)/,
  /^url\.query$/,
  /^url\.fragment$/,
  /^gen_ai\.(prompt|completion|input|output|system_instructions)/,
  /^enduser\./,
  /^user\.(email|name|full_name|hash)$/,
  /(^|[._-])(authorization|cookie|set-cookie|password|passwd|secret|token|api[_-]?key|apikey|credential|session)([._-]|$)/i,
];

/** Attributes whose value is a URL or request target: the query is removed. */
const URL_KEYS: ReadonlySet<string> = new Set([
  "http.url",
  "http.target",
  "url.full",
  "url.original",
  "url.path",
  "resource.name",
  "next.span_name",
]);

const SECRET_PATTERNS: ReadonlyArray<[RegExp, string]> = [
  [/\b(Bearer|Basic|DeepL-Auth-Key)\s+[A-Za-z0-9._~+/=:-]+/gi, `$1 ${REDACTED}`],
  [/\bsk-[A-Za-z0-9_-]{8,}/g, REDACTED],
  [/\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*/g, REDACTED],
  [/\b(gms_session|gms_auth_state|gms_pkce_verifier)=[^;\s]*/g, `$1=${REDACTED}`],
  [
    /([?&;](?:code|state|token|access_token|id_token|refresh_token|q|key|api_key|password|secret|auth|signature)=)[^&#\s"']*/gi,
    `$1${REDACTED}`,
  ],
];

export function isDroppedKey(key: string): boolean {
  if (key.startsWith("gms.")) {
    return !ALLOWED_GMS_ATTRIBUTE_KEYS.has(key);
  }
  return DROPPED_KEY_PATTERNS.some((pattern) => pattern.test(key));
}

/** Remove query string and fragment from every URL-like token in `value`. */
export function stripQuery(value: string): string {
  return value.replace(/(\S)[?#]\S*/g, "$1");
}

export function scrubSecrets(value: string): string {
  let result = value;
  for (const [pattern, replacement] of SECRET_PATTERNS) {
    result = result.replace(pattern, replacement);
  }
  return result;
}

function truncate(value: string, max: number): string {
  return value.length <= max ? value : `${value.slice(0, max)}…[truncated]`;
}

export function sanitizeString(value: string, max = MAX_STRING_LENGTH): string {
  return truncate(scrubSecrets(value), max);
}

function sanitizeValue(key: string, value: AttributeValue): AttributeValue {
  if (typeof value === "string") {
    return sanitizeString(URL_KEYS.has(key) ? stripQuery(value) : value);
  }
  if (Array.isArray(value)) {
    const items = value.slice(0, MAX_ARRAY_LENGTH);
    if (items.every((item) => typeof item === "string" || item === null || item === undefined)) {
      return (items as Array<string | null | undefined>).map((item) =>
        typeof item === "string" ? sanitizeString(item) : item,
      ) as AttributeValue;
    }
    return items as AttributeValue;
  }
  return value;
}

export function sanitizeAttributes(attributes: Attributes | undefined): Attributes {
  const result: Attributes = {};
  if (!attributes) {
    return result;
  }
  for (const [key, value] of Object.entries(attributes)) {
    if (value === undefined || isDroppedKey(key)) {
      continue;
    }
    result[key] = sanitizeValue(key, value);
  }
  return result;
}

/** Keep only `at …` frame lines; the first line of a stack is the message. */
export function sanitizeStacktrace(stack: string): string {
  const frames = stack
    .split("\n")
    .filter((line) => /^\s+at\s/.test(line))
    .map((line) => scrubSecrets(line));
  return truncate(frames.join("\n"), MAX_STACKTRACE_LENGTH);
}

function sanitizeEvent(event: TimedEvent): TimedEvent {
  if (event.name !== "exception") {
    return { ...event, name: sanitizeString(stripQuery(event.name)), attributes: sanitizeAttributes(event.attributes) };
  }
  const source = event.attributes ?? {};
  const attributes: Attributes = {};
  if (typeof source["exception.type"] === "string") {
    attributes["exception.type"] = sanitizeString(source["exception.type"], 128);
  }
  if (source["exception.message"] !== undefined) {
    attributes["exception.message"] = REDACTED;
  }
  if (typeof source["exception.stacktrace"] === "string") {
    attributes["exception.stacktrace"] = sanitizeStacktrace(source["exception.stacktrace"]);
  }
  return { ...event, attributes };
}

function sanitizeStatus(status: SpanStatus): SpanStatus {
  // Status messages commonly contain `error.message`; keep only error codes.
  if (status.message !== undefined && !/^[A-Z][A-Z0-9_]{0,63}$/.test(status.message)) {
    return { code: status.code };
  }
  return status;
}

function sanitizeLink(link: Link): Link {
  return { ...link, attributes: sanitizeAttributes(link.attributes) };
}

export function sanitizeSpanName(name: string): string {
  return sanitizeString(stripQuery(name), 128);
}

export function sanitizeSpan(span: ReadableSpan): ReadableSpan {
  return {
    name: sanitizeSpanName(span.name),
    kind: span.kind,
    spanContext: () => span.spanContext(),
    parentSpanContext: span.parentSpanContext,
    startTime: span.startTime,
    endTime: span.endTime,
    status: sanitizeStatus(span.status),
    attributes: sanitizeAttributes(span.attributes),
    links: span.links.map(sanitizeLink),
    events: span.events.map(sanitizeEvent),
    duration: span.duration,
    ended: span.ended,
    resource: span.resource,
    instrumentationScope: span.instrumentationScope,
    droppedAttributesCount: span.droppedAttributesCount,
    droppedEventsCount: span.droppedEventsCount,
    droppedLinksCount: span.droppedLinksCount,
  };
}

type ExportCallback = Parameters<SpanExporter["export"]>[1];

/** ExportResultCode.FAILED from @opentelemetry/core (kept local on purpose). */
const EXPORT_FAILED = 1;

/** Wraps the real OTLP exporter; every span is sanitised before export. */
export class RedactingSpanExporter implements SpanExporter {
  private readonly delegate: SpanExporter;

  constructor(delegate: SpanExporter) {
    this.delegate = delegate;
  }

  export(spans: ReadableSpan[], resultCallback: ExportCallback): void {
    let sanitized: ReadableSpan[];
    try {
      sanitized = spans.map(sanitizeSpan);
    } catch (error) {
      // Fail closed: never forward an unsanitised span.
      resultCallback({ code: EXPORT_FAILED, error: error instanceof Error ? new Error("Span redaction failed") : undefined });
      return;
    }
    this.delegate.export(sanitized, resultCallback);
  }

  shutdown(): Promise<void> {
    return this.delegate.shutdown();
  }

  forceFlush(): Promise<void> {
    return this.delegate.forceFlush?.() ?? Promise.resolve();
  }
}
