/**
 * Low-frequency runtime and SQLite storage gauges (As-To-Be §5.6, §5.9).
 *
 * Collected only when the MetricReader exports (default every 30 s). Uses
 * `fs.statSync` / `fs.statfsSync` on the database files; it never opens or
 * queries the production database for monitoring purposes.
 */

import { statfsSync, statSync } from "node:fs";
import { dirname } from "node:path";
import { monitorEventLoopDelay, type IntervalHistogram } from "node:perf_hooks";

import { metrics, type ObservableResult } from "@opentelemetry/api";

import { resolveDatabasePath } from "../db/sqlite.ts";
import { ATTR, INSTRUMENTATION_SCOPE, METRIC } from "./schema.ts";

let histogram: IntervalHistogram | undefined;

const EVENT_LOOP_RESOLUTION_MS = 20;

/**
 * `monitorEventLoopDelay` samples with a timer every `resolution` ms and
 * records the elapsed interval, so an idle loop reads ≈ resolution
 * (measured on Node 26: idle min 19.5 ms at resolution 20; a 500 ms block
 * read as 515 ms). Subtract the resolution to report actual delay.
 */
export function eventLoopDelaySeconds(recordedNs: number, resolutionMs = EVENT_LOOP_RESOLUTION_MS): number {
  if (!Number.isFinite(recordedNs)) {
    return 0;
  }
  return Math.max(0, recordedNs - resolutionMs * 1e6) / 1e9;
}

function fileSize(path: string): number | undefined {
  try {
    return statSync(path).size;
  } catch {
    return undefined;
  }
}

function databasePath(): string | undefined {
  try {
    return resolveDatabasePath();
  } catch {
    return undefined;
  }
}

export function startRuntimeMetrics(): void {
  if (histogram) {
    return;
  }
  const meter = metrics.getMeter(INSTRUMENTATION_SCOPE);
  histogram = monitorEventLoopDelay({ resolution: EVENT_LOOP_RESOLUTION_MS });
  histogram.enable();
  const loop = histogram;

  const nsToSeconds = eventLoopDelaySeconds;
  const p50 = meter.createObservableGauge(METRIC.eventLoopDelayP50, { unit: "s", description: "Event loop delay p50 since last collection" });
  const p99 = meter.createObservableGauge(METRIC.eventLoopDelayP99, { unit: "s", description: "Event loop delay p99 since last collection" });
  const max = meter.createObservableGauge(METRIC.eventLoopDelayMax, { unit: "s", description: "Event loop delay max since last collection" });
  meter.addBatchObservableCallback(
    (observer) => {
      if (loop.count > 0) {
        observer.observe(p50, nsToSeconds(loop.percentile(50)));
        observer.observe(p99, nsToSeconds(loop.percentile(99)));
        observer.observe(max, nsToSeconds(loop.max));
      }
      loop.reset();
    },
    [p50, p99, max],
  );

  meter
    .createObservableUpDownCounter(METRIC.processMemoryUsage, { unit: "By", description: "Process resident set size" })
    .addCallback((result: ObservableResult) => result.observe(process.memoryUsage.rss()));
  meter
    .createObservableUpDownCounter(METRIC.v8HeapUsed, { unit: "By", description: "V8 heap used" })
    .addCallback((result: ObservableResult) => result.observe(process.memoryUsage().heapUsed));
  meter
    .createObservableGauge(METRIC.processUptime, { unit: "s", description: "Process uptime; resets reveal restarts" })
    .addCallback((result: ObservableResult) => result.observe(process.uptime()));

  meter
    .createObservableGauge(METRIC.sqliteFileSize, { unit: "By", description: "SQLite main / WAL / SHM file size" })
    .addCallback((result: ObservableResult) => {
      const path = databasePath();
      if (!path) return;
      for (const [file, suffix] of [["db", ""], ["wal", "-wal"], ["shm", "-shm"]] as const) {
        const size = fileSize(`${path}${suffix}`);
        if (size !== undefined) {
          result.observe(size, { [ATTR.sqliteFile]: file });
        }
      }
    });
  meter
    .createObservableGauge(METRIC.sqliteFilesystemAvailable, {
      unit: "By",
      description: "Available bytes on the filesystem holding the SQLite database",
    })
    .addCallback((result: ObservableResult) => {
      const path = databasePath();
      if (!path) return;
      try {
        const stats = statfsSync(dirname(path));
        result.observe(Number(stats.bavail) * Number(stats.bsize));
      } catch {
        // Missing mount is reported by the application itself (503).
      }
    });
}
