/**
 * OTLP/HTTP exporters for traces and metrics.
 *
 * Both signals use the official OpenTelemetry Node.js HTTP transport
 * (`@opentelemetry/otlp-exporter-base`, `node:http` / `node:https`), so they
 * share exactly the same, specification-defined environment handling:
 *
 * - Headers: `OTEL_EXPORTER_OTLP_HEADERS`, overridden per key by
 *   `OTEL_EXPORTER_OTLP_{TRACES,METRICS}_HEADERS` (e.g. the Collector's
 *   `authorization=Bearer%20<token>`). Header values are never logged here.
 * - TLS trust: `OTEL_EXPORTER_OTLP_CERTIFICATE` (PEM CA bundle) or the
 *   signal-specific `OTEL_EXPORTER_OTLP_{TRACES,METRICS}_CERTIFICATE`.
 *   `NODE_EXTRA_CA_CERTS` also works but widens trust for the whole process.
 * - mTLS (optional): `OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE` /
 *   `OTEL_EXPORTER_OTLP_CLIENT_KEY` (and signal-specific variants).
 * - Compression: `OTEL_EXPORTER_OTLP_COMPRESSION` (default none).
 *
 * Endpoints and timeouts are resolved by `config.ts` and passed explicitly.
 *
 * Why not `@vercel/otel`'s `OTLPHttpProtoTraceExporter`: it sends through the
 * global `fetch` and ignores the certificate variables, so with only
 * `OTEL_EXPORTER_OTLP_CERTIFICATE` set, metrics were delivered while every
 * trace export failed TLS verification silently (release readiness check,
 * 2026-11-13).
 */

import { OTLPMetricExporter } from "@opentelemetry/exporter-metrics-otlp-http";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-proto";

import type { TelemetryConfig } from "./config.ts";

type ExporterConfig = Pick<TelemetryConfig, "tracesEndpoint" | "metricsEndpoint" | "exportTimeoutMillis">;

/** OTLP/HTTP protobuf trace exporter (`POST <endpoint>/v1/traces`). */
export function createTraceExporter(config: ExporterConfig): OTLPTraceExporter {
  return new OTLPTraceExporter({
    url: config.tracesEndpoint,
    timeoutMillis: config.exportTimeoutMillis,
  });
}

/** OTLP/HTTP JSON metric exporter (`POST <endpoint>/v1/metrics`), cumulative temporality. */
export function createMetricExporter(config: ExporterConfig): OTLPMetricExporter {
  return new OTLPMetricExporter({
    url: config.metricsEndpoint,
    timeoutMillis: config.exportTimeoutMillis,
  });
}
