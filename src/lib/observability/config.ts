/**
 * Runtime telemetry configuration for the GMS v5 application process.
 *
 * Every value comes from environment variables so that SRE / Platform can
 * point the application at a different Collector without a rebuild. Telemetry
 * is opt-in: unless `GMS_OTEL_ENABLED=true`, nothing is registered and the
 * OpenTelemetry API stays a no-op.
 *
 * Schema and naming rules: docs/observability/application-instrumentation.md
 */

type Env = Record<string, string | undefined>;

export type SamplerName =
  | "always_on"
  | "always_off"
  | "traceidratio"
  | "parentbased_always_on"
  | "parentbased_always_off"
  | "parentbased_traceidratio";

export type TelemetryConfig = {
  enabled: boolean;
  serviceName: string;
  serviceVersion: string;
  deploymentEnvironment: string;
  tracesEndpoint: string;
  metricsEndpoint: string;
  exportTimeoutMillis: number;
  metricsEnabled: boolean;
  metricsExportIntervalMillis: number;
  sampler: SamplerName;
  samplerRatio: number;
  rumEnabled: boolean;
};

const SAMPLERS: readonly SamplerName[] = [
  "always_on",
  "always_off",
  "traceidratio",
  "parentbased_always_on",
  "parentbased_always_off",
  "parentbased_traceidratio",
];

const DEFAULT_OTLP_ENDPOINT = "http://localhost:4318";

export function isTelemetryEnabled(env: Env = process.env): boolean {
  return env.GMS_OTEL_ENABLED === "true";
}

/** Server-side acceptance of Browser RUM beacons. Requires telemetry too. */
export function isRumIngestionEnabled(env: Env = process.env): boolean {
  return isTelemetryEnabled(env) && env.GMS_RUM_ENABLED === "true";
}

function nonEmpty(value: string | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

function positiveInteger(value: string | undefined, fallback: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || !Number.isInteger(parsed) || parsed <= 0) {
    return fallback;
  }
  return Math.min(parsed, max);
}

function ratio(value: string | undefined): number {
  const parsed = Number(value);
  if (value === undefined || value.trim() === "" || !Number.isFinite(parsed) || parsed < 0 || parsed > 1) {
    return 1;
  }
  return parsed;
}

function signalEndpoint(env: Env, signalVariable: string, path: "/v1/traces" | "/v1/metrics"): string {
  const specific = nonEmpty(env[signalVariable]);
  if (specific) {
    return specific;
  }
  const base = (nonEmpty(env.OTEL_EXPORTER_OTLP_ENDPOINT) ?? DEFAULT_OTLP_ENDPOINT).replace(/\/+$/, "");
  return `${base}${path}`;
}

export function readTelemetryConfig(env: Env = process.env): TelemetryConfig {
  const samplerValue = nonEmpty(env.OTEL_TRACES_SAMPLER)?.toLowerCase();
  const sampler = SAMPLERS.includes(samplerValue as SamplerName)
    ? (samplerValue as SamplerName)
    : "parentbased_always_on";

  return {
    enabled: isTelemetryEnabled(env),
    serviceName: nonEmpty(env.OTEL_SERVICE_NAME) ?? "gms-v5",
    serviceVersion: nonEmpty(env.GMS_RELEASE_VERSION) ?? "unknown",
    deploymentEnvironment:
      nonEmpty(env.GMS_DEPLOYMENT_ENVIRONMENT) ?? nonEmpty(env.NODE_ENV) ?? "development",
    tracesEndpoint: signalEndpoint(env, "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "/v1/traces"),
    metricsEndpoint: signalEndpoint(env, "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "/v1/metrics"),
    exportTimeoutMillis: positiveInteger(env.OTEL_EXPORTER_OTLP_TIMEOUT, 10_000, 30_000),
    metricsEnabled: env.GMS_OTEL_METRICS_ENABLED !== "false",
    metricsExportIntervalMillis: positiveInteger(
      env.GMS_OTEL_METRICS_EXPORT_INTERVAL_MS ?? env.OTEL_METRIC_EXPORT_INTERVAL,
      30_000,
      300_000,
    ),
    sampler,
    samplerRatio: ratio(env.OTEL_TRACES_SAMPLER_ARG),
    rumEnabled: isRumIngestionEnabled(env),
  };
}
