/**
 * GMS-owned metric instruments.
 *
 * The OpenTelemetry JS metrics API has no proxy provider: a Meter obtained
 * before `registerOTel()` stays a no-op forever. Instruments are therefore
 * resolved lazily and re-created whenever the global MeterProvider changes
 * (startup, and test harnesses that install their own provider).
 *
 * Cardinality rule (As-To-Be §9.2): attributes are bounded enums only —
 * never user / glossary / term / request / trace IDs, raw URLs or messages.
 */

import { metrics, type Attributes, type Counter, type Histogram, type MeterProvider } from "@opentelemetry/api";

import { INSTRUMENTATION_SCOPE, METRIC, RUM_WEB_VITALS, type RumWebVitalName } from "./schema.ts";

const LATENCY_BUCKETS_SECONDS = [
  0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10, 20, 30, 60, 120,
];
const DB_BUCKETS_SECONDS = [
  0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 5,
];
const TERM_COUNT_BUCKETS = [0, 1, 10, 50, 100, 500, 1000, 2500, 5000, 7500, 10000];
const CLS_BUCKETS = [0, 0.01, 0.05, 0.1, 0.15, 0.25, 0.5, 1];

export type Instruments = {
  httpServerDuration: Histogram;
  dbClientDuration: Histogram;
  translationDuration: Histogram;
  translationSelectedTerms: Histogram;
  externalDuration: Histogram;
  rumActionDuration: Histogram;
  rumWebVitals: Record<RumWebVitalName, Histogram>;
  rumEventsRejected: Counter;
};

let cache: { provider: MeterProvider; instruments: Instruments } | undefined;

function create(provider: MeterProvider): Instruments {
  const meter = provider.getMeter(INSTRUMENTATION_SCOPE);
  const seconds = (name: string, description: string, buckets = LATENCY_BUCKETS_SECONDS) =>
    meter.createHistogram(name, {
      description,
      unit: "s",
      advice: { explicitBucketBoundaries: buckets },
    });

  const rumWebVitals = Object.fromEntries(
    RUM_WEB_VITALS.map((name) => [
      name,
      name === "CLS"
        ? meter.createHistogram(`${METRIC.rumWebVitalPrefix}cls`, {
            description: "Browser Cumulative Layout Shift (unitless)",
            unit: "1",
            advice: { explicitBucketBoundaries: CLS_BUCKETS },
          })
        : seconds(`${METRIC.rumWebVitalPrefix}${name.toLowerCase()}`, `Browser Web Vital ${name}`),
    ]),
  ) as Record<RumWebVitalName, Histogram>;

  return {
    httpServerDuration: seconds(METRIC.httpServerDuration, "Duration of HTTP server requests handled by Next.js"),
    dbClientDuration: seconds(METRIC.dbClientDuration, "Duration of SQLite statement execution", DB_BUCKETS_SECONDS),
    translationDuration: seconds(METRIC.translationDuration, "Server-side duration of the BYO translation use case"),
    translationSelectedTerms: meter.createHistogram(METRIC.translationSelectedTerms, {
      description: "Number of selected Term IDs per translation request",
      unit: "{term}",
      advice: { explicitBucketBoundaries: TERM_COUNT_BUCKETS },
    }),
    externalDuration: seconds(METRIC.externalDuration, "Client-side duration of DeepL / OpenAI HTTP calls"),
    rumActionDuration: seconds(METRIC.rumActionDuration, "Browser user action duration from input to rendered result"),
    rumWebVitals,
    rumEventsRejected: meter.createCounter(METRIC.rumEventsRejected, {
      description: "Browser RUM events rejected by server-side validation",
      unit: "{event}",
    }),
  };
}

export function instruments(): Instruments {
  const provider = metrics.getMeterProvider();
  if (!cache || cache.provider !== provider) {
    cache = { provider, instruments: create(provider) };
  }
  return cache.instruments;
}

/** Never let a metrics failure break a request. */
export function safeRecord(record: (instruments: Instruments) => void): void {
  try {
    record(instruments());
  } catch {
    // Telemetry must not affect business processing (As-To-Be §9.4).
  }
}

export function secondsSince(startMs: number): number {
  return Math.max(0, (performance.now() - startMs) / 1000);
}

export type { Attributes };
