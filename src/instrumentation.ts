/**
 * Next.js instrumentation hook. Telemetry is opt-in (`GMS_OTEL_ENABLED=true`)
 * and Node.js-runtime only; when disabled, the OpenTelemetry API remains a
 * no-op and the application behaves exactly as without instrumentation.
 *
 * Build-graph constraint: Next.js compiles this file for every runtime
 * (Node.js and Edge). `node-sdk` transitively imports `node:` built-ins
 * (`runtime-metrics.ts` → `node:fs`), which the Edge compiler cannot resolve.
 * Webpack inlines `process.env.NEXT_RUNTIME` and drops only the *untaken
 * branch of an `if`* — code after an early `return` is still parsed. The
 * dynamic import must therefore stay lexically inside the
 * `if (process.env.NEXT_RUNTIME === "nodejs")` block (official Next.js
 * pattern). Guarded by a regression test in test/observability.test.ts.
 *
 * Configuration: docs/observability/application-instrumentation.md
 */
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    if (process.env.GMS_OTEL_ENABLED !== "true") {
      return;
    }

    try {
      const { registerGmsTelemetry } = await import("./lib/observability/node-sdk");
      registerGmsTelemetry();
    } catch (error) {
      // Observability failure must never prevent the application from serving.
      console.error(
        "Telemetry initialisation failed; continuing without telemetry",
        error instanceof Error ? error.name : "UnknownError",
      );
    }
  }
}
