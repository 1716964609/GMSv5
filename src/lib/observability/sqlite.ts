/**
 * `node:sqlite` instrumentation (As-To-Be §5.6).
 *
 * No generic OpenTelemetry instrumentation exists for `node:sqlite`, so the
 * DatabaseSync instance is wrapped at the point the application opens it
 * (`src/lib/db/client.ts`). Repository code is unchanged.
 *
 * Per statement execution (`run` / `get` / `all` / `iterate` / `exec`):
 * - metric `db.client.operation.duration` (always, independent of sampling)
 * - CLIENT span named by `db.query.summary`, e.g. `SELECT terms`, only when
 *   a recording parent span exists (no orphan root traces from scripts) and
 *   the per-trace budget is not exhausted (bulk imports can run thousands of
 *   INSERTs per request).
 *
 * Privacy: neither SQL text (`db.query.text`) nor parameters are recorded.
 * The summary contains only the SQL keyword and a validated identifier.
 *
 * Because `node:sqlite` is synchronous, statement time is also event-loop
 * blocking time; correlate with `nodejs.eventloop.delay.*`.
 */

import type { DatabaseSync, StatementSync } from "node:sqlite";

import { context, SpanKind, SpanStatusCode, trace, type Attributes } from "@opentelemetry/api";

import { safeRecord } from "./metrics.ts";
import { ATTR, INSTRUMENTATION_SCOPE } from "./schema.ts";

const tracer = trace.getTracer(INSTRUMENTATION_SCOPE);
const INSTRUMENTED = Symbol.for("gms.sqlite.instrumented");

export const SQLITE_SPAN_BUDGET_PER_TRACE = 100;
const BUDGET_TRACKED_TRACES = 1000;
const spanBudget = new Map<string, number>();

export type QuerySummary = {
  operation: string;
  collection?: string;
  summary: string;
};

const IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
const OPERATIONS = new Set([
  "SELECT", "INSERT", "UPDATE", "DELETE", "REPLACE", "WITH", "PRAGMA", "BEGIN",
  "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE", "CREATE", "ALTER", "DROP", "VACUUM", "ANALYZE",
]);

function identifier(match: RegExpMatchArray | null): string | undefined {
  const value = match?.[1]?.replace(/["`[\]]/g, "");
  return value && IDENTIFIER.test(value) ? value : undefined;
}

const summaryCache = new Map<string, QuerySummary>();

/** Derive a low-cardinality, data-free summary from a SQL statement. */
export function summarizeSql(sql: string): QuerySummary {
  const cached = summaryCache.get(sql);
  if (cached) {
    return cached;
  }
  const head = sql.replace(/^(\s|--[^\n]*\n|\/\*[\s\S]*?\*\/)+/, "").slice(0, 512);
  const keyword = head.match(/^([A-Za-z]+)/)?.[1]?.toUpperCase() ?? "OTHER";
  const operation = OPERATIONS.has(keyword) ? keyword : "OTHER";
  let collection: string | undefined;
  switch (operation) {
    case "SELECT":
    case "DELETE":
    case "WITH":
      collection = identifier(head.match(/\bFROM\s+([A-Za-z_"`[][\w"`\]]*)/i));
      break;
    case "INSERT":
    case "REPLACE":
      collection = identifier(head.match(/\bINTO\s+([A-Za-z_"`[][\w"`\]]*)/i));
      break;
    case "UPDATE":
      collection = identifier(head.match(/^UPDATE\s+(?:OR\s+\w+\s+)?([A-Za-z_"`[][\w"`\]]*)/i));
      break;
    case "PRAGMA":
      collection = identifier(head.match(/^PRAGMA\s+([A-Za-z_]\w*)/i));
      break;
    case "CREATE":
    case "ALTER":
    case "DROP":
      collection = identifier(
        head.match(/^(?:CREATE|ALTER|DROP)\s+(?:UNIQUE\s+)?(?:TABLE|INDEX|TRIGGER|VIEW)\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?([A-Za-z_"`[][\w"`\]]*)/i),
      );
      break;
    default:
      collection = undefined;
  }
  const result: QuerySummary = {
    operation,
    collection,
    summary: collection ? `${operation} ${collection}` : operation,
  };
  if (sql.length <= 4096 && summaryCache.size < 500) {
    summaryCache.set(sql, result);
  }
  return result;
}

const PRIMARY_RESULT_CODES: Record<number, string> = {
  1: "SQLITE_ERROR",
  5: "SQLITE_BUSY",
  6: "SQLITE_LOCKED",
  7: "SQLITE_NOMEM",
  8: "SQLITE_READONLY",
  10: "SQLITE_IOERR",
  11: "SQLITE_CORRUPT",
  13: "SQLITE_FULL",
  14: "SQLITE_CANTOPEN",
  19: "SQLITE_CONSTRAINT",
};

/** Map a node:sqlite error to a bounded `error.type` (never the message). */
export function sqliteErrorType(error: unknown): { errorType: string; statusCode?: string } {
  const errcode = (error as { errcode?: unknown })?.errcode;
  if (typeof errcode === "number" && Number.isInteger(errcode)) {
    const primary = errcode & 0xff;
    return { errorType: PRIMARY_RESULT_CODES[primary] ?? `SQLITE_${primary}`, statusCode: String(errcode) };
  }
  return { errorType: error instanceof Error && IDENTIFIER.test(error.name) ? error.name : "Error" };
}

function takeSpanBudget(traceId: string): boolean {
  const used = spanBudget.get(traceId) ?? 0;
  if (used === 0 && spanBudget.size >= BUDGET_TRACKED_TRACES) {
    spanBudget.clear();
  }
  spanBudget.set(traceId, used + 1);
  return used < SQLITE_SPAN_BUDGET_PER_TRACE;
}

function measure<T>(summary: QuerySummary, execute: () => T): T {
  const parent = trace.getSpan(context.active());
  const attributes: Attributes = {
    "db.system.name": "sqlite",
    "db.operation.name": summary.operation,
    "db.query.summary": summary.summary,
    ...(summary.collection ? { "db.collection.name": summary.collection } : {}),
  };
  let span = undefined;
  if (parent?.isRecording()) {
    if (takeSpanBudget(parent.spanContext().traceId)) {
      span = tracer.startSpan(summary.summary, { kind: SpanKind.CLIENT, attributes });
    } else if ((spanBudget.get(parent.spanContext().traceId) ?? 0) === SQLITE_SPAN_BUDGET_PER_TRACE + 1) {
      parent.setAttribute(ATTR.sqliteSpansSuppressed, true);
    }
  }
  const start = performance.now();
  let errorType: string | undefined;
  try {
    return execute();
  } catch (error) {
    const classified = sqliteErrorType(error);
    errorType = classified.errorType;
    if (span) {
      span.setAttributes({
        "error.type": classified.errorType,
        ...(classified.statusCode ? { "db.response.status_code": classified.statusCode } : {}),
      });
      span.setStatus({ code: SpanStatusCode.ERROR, message: classified.errorType });
    }
    throw error;
  } finally {
    const seconds = (performance.now() - start) / 1000;
    span?.end();
    safeRecord(({ dbClientDuration }) =>
      dbClientDuration.record(seconds, {
        "db.system.name": "sqlite",
        "db.operation.name": summary.operation,
        ...(summary.collection ? { "db.collection.name": summary.collection } : {}),
        ...(errorType ? { "error.type": errorType } : {}),
      }),
    );
  }
}

type StatementMethod = "run" | "get" | "all" | "iterate";

function instrumentStatement(statement: StatementSync, summary: QuerySummary): StatementSync {
  const target = statement as unknown as Record<StatementMethod, ((...args: unknown[]) => unknown) | undefined>;
  for (const method of ["run", "get", "all", "iterate"] as const) {
    const original = target[method];
    if (typeof original !== "function") {
      continue;
    }
    // `iterate` returns a lazy iterator; only its creation is measured.
    target[method] = function instrumented(this: unknown, ...args: unknown[]) {
      return measure(summary, () => original.apply(statement, args));
    };
  }
  return statement;
}

/**
 * Instrument a DatabaseSync instance in place and return it. Idempotent.
 * Instrumentation failure never prevents database access.
 */
export function instrumentDatabase(db: DatabaseSync): DatabaseSync {
  const target = db as unknown as Record<PropertyKey, unknown> & DatabaseSync;
  if (target[INSTRUMENTED]) {
    return db;
  }
  try {
    const prepare = db.prepare;
    const exec = db.exec;
    target.prepare = function instrumentedPrepare(sql: string): StatementSync {
      const statement = prepare.call(db, sql);
      try {
        return instrumentStatement(statement, summarizeSql(sql));
      } catch {
        return statement;
      }
    };
    target.exec = function instrumentedExec(sql: string): void {
      return measure(summarizeSql(sql), () => exec.call(db, sql));
    };
    Object.defineProperty(target, INSTRUMENTED, { value: true });
  } catch {
    // Leave the database uninstrumented rather than failing the request.
  }
  return db;
}
