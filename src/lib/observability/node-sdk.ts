/**
 * OpenTelemetry SDK registration for the Next.js Node.js runtime.
 * Imported dynamically from `src/instrumentation.ts` only when
 * `GMS_OTEL_ENABLED=true` and `NEXT_RUNTIME=nodejs`.
 *
 * Design notes (see docs/observability/application-instrumentation.md):
 * - `spanProcessors` is set explicitly. With the "auto" processor,
 *   @vercel/otel adds its own *unredacted* OTLP exporter whenever
 *   OTEL_EXPORTER_OTLP_ENDPOINT is set — that path must not exist.
 * - Only the W3C `tracecontext` propagator is enabled: untrusted inbound
 *   `baggage` is neither accepted nor forwarded.
 * - Outgoing trace context is never propagated to third parties
 *   (DeepL / OpenAI / Cognito).
 * - Export is bounded (queue size, timeout) and asynchronous; Collector
 *   outages drop telemetry and never fail requests (As-To-Be §9.4).
 * - Exporter transport, OTLP headers and TLS trust: see `exporters.ts`.
 */

import { PeriodicExportingMetricReader } from "@opentelemetry/sdk-metrics";
import { BatchSpanProcessor } from "@opentelemetry/sdk-trace-base";
import { registerOTel } from "@vercel/otel";

import { readTelemetryConfig, type TelemetryConfig } from "./config.ts";
import { createMetricExporter, createTraceExporter } from "./exporters.ts";
import { RedactingSpanExporter } from "./redaction.ts";
import { startRuntimeMetrics } from "./runtime-metrics.ts";
import { ATTR, TELEMETRY_SCHEMA_VERSION } from "./schema.ts";
import {
  createBaseSampler,
  GmsSampler,
  HttpServerMetricsProcessor,
  RumAwareIdGenerator,
} from "./tracing.ts";

let registered = false;

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function originOf(url: string): string | undefined {
  try {
    return new URL(url).origin;
  } catch {
    return undefined;
  }
}

export function registerGmsTelemetry(config: TelemetryConfig = readTelemetryConfig()): boolean {
  if (registered || !config.enabled) {
    return false;
  }
  registered = true;

  const exportOrigins = [originOf(config.tracesEndpoint), originOf(config.metricsEndpoint)].filter(
    (origin): origin is string => Boolean(origin),
  );

  const traceExporter = new RedactingSpanExporter(createTraceExporter(config));

  registerOTel({
    serviceName: config.serviceName,
    attributes: {
      "service.namespace": "gms",
      "service.version": config.serviceVersion,
      "deployment.environment.name": config.deploymentEnvironment,
      [ATTR.schemaVersion]: TELEMETRY_SCHEMA_VERSION,
    },
    propagators: ["tracecontext"],
    traceSampler: new GmsSampler(createBaseSampler(config.sampler, config.samplerRatio)),
    idGenerator: new RumAwareIdGenerator(),
    spanProcessors: [
      new HttpServerMetricsProcessor(),
      new BatchSpanProcessor(traceExporter, {
        maxQueueSize: 2048,
        maxExportBatchSize: 512,
        scheduledDelayMillis: 5000,
        exportTimeoutMillis: config.exportTimeoutMillis,
      }),
    ],
    metricReaders: config.metricsEnabled
      ? [
          new PeriodicExportingMetricReader({
            exporter: createMetricExporter(config),
            exportIntervalMillis: config.metricsExportIntervalMillis,
            exportTimeoutMillis: Math.min(config.exportTimeoutMillis, config.metricsExportIntervalMillis),
          }),
        ]
      : undefined,
    instrumentationConfig: {
      fetch: {
        // Do not trace the telemetry export itself.
        ignoreUrls: exportOrigins.map((origin) => new RegExp(`^${escapeRegExp(origin)}`)),
        // Never send traceparent to DeepL, OpenAI, Cognito or any third party.
        dontPropagateContextUrls: [/.*/],
      },
    },
  });

  if (config.metricsEnabled) {
    startRuntimeMetrics();
  }
  return true;
}
