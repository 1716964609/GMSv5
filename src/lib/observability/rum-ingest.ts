/**
 * Server-side ingestion of Browser RUM beacons (As-To-Be §5.1, §6.2).
 *
 * Browsers never talk to the Collector directly: the Observability endpoint
 * stays private (As-To-Be §9.3). Authenticated, same-origin beacons are
 * posted to `/api/telemetry/rum`, validated against a strict allowlist
 * schema here, and re-emitted as server-side metrics (and, for sampled user
 * actions, a span carrying the browser's own trace / span IDs).
 *
 * Nothing free-form from the browser is exported: names, outcomes, routes
 * and ratings are enums; numbers are range-checked; unknown fields cause
 * the event to be rejected.
 *
 * Timing basis: browser and server clocks differ. The browser sends the
 * action duration and how long ago it ended (both from its monotonic
 * `performance.now()`); the server anchors the span on its receive time.
 * Span start/end are therefore estimates within network transit time;
 * the duration itself is exact (`gms.rum.timing_basis`).
 */

import { ROOT_CONTEXT, SpanKind, SpanStatusCode, trace, type Attributes } from "@opentelemetry/api";

import { safeRecord } from "./metrics.ts";
import {
  ATTR,
  INSTRUMENTATION_SCOPE,
  RUM_ACTIONS,
  RUM_PAGE_ROUTES,
  RUM_WEB_VITALS,
  SPAN,
  type RumActionName,
  type RumPageRoute,
  type RumWebVitalName,
} from "./schema.ts";
import { FORCE_SAMPLE_KEY, withForcedSpanIds } from "./tracing.ts";

export const RUM_SCHEMA_VERSION = 1;
export const RUM_MAX_BODY_BYTES = 16 * 1024;
export const RUM_MAX_EVENTS = 20;
const MAX_DURATION_MS = 10 * 60 * 1000;
const MAX_ENDED_AGO_MS = 10 * 60 * 1000;
const TRACEPARENT = /^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$/;

export type RumActionEvent = {
  type: "action";
  name: RumActionName;
  outcome: string;
  durationMs: number;
  endedAgoMs: number;
  route: RumPageRoute;
  httpStatus?: number;
  traceparent?: string;
};

export type RumVitalEvent = {
  type: "vital";
  name: RumWebVitalName;
  value: number;
  rating: "good" | "needs-improvement" | "poor";
  route: RumPageRoute;
};

export type RumEvent = RumActionEvent | RumVitalEvent;

export type RejectReason =
  | "invalid_shape"
  | "unknown_field"
  | "invalid_name"
  | "invalid_outcome"
  | "invalid_number"
  | "invalid_route"
  | "invalid_traceparent"
  | "too_many_events";

export type RumValidationResult = {
  events: RumEvent[];
  rejected: Partial<Record<RejectReason, number>>;
};

const ACTION_KEYS = new Set(["type", "name", "outcome", "durationMs", "endedAgoMs", "route", "httpStatus", "traceparent"]);
const VITAL_KEYS = new Set(["type", "name", "value", "rating", "route"]);
const RATINGS = new Set(["good", "needs-improvement", "poor"]);
const ROUTES = new Set<string>([...RUM_PAGE_ROUTES, "other"]);

class Rejection extends Error {
  readonly reason: RejectReason;
  constructor(reason: RejectReason) {
    super(reason);
    this.reason = reason;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function finiteInRange(value: unknown, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max) {
    throw new Rejection("invalid_number");
  }
  return value;
}

function onlyKeys(record: Record<string, unknown>, allowed: Set<string>): void {
  for (const key of Object.keys(record)) {
    if (!allowed.has(key)) {
      throw new Rejection("unknown_field");
    }
  }
}

function route(value: unknown): RumPageRoute {
  if (typeof value !== "string" || !ROUTES.has(value)) {
    throw new Rejection("invalid_route");
  }
  return value as RumPageRoute;
}

export function parseTraceparent(value: string): { traceId: string; spanId: string; sampled: boolean } | null {
  const match = TRACEPARENT.exec(value);
  if (!match || /^0+$/.test(match[1]) || /^0+$/.test(match[2])) {
    return null;
  }
  return { traceId: match[1], spanId: match[2], sampled: (parseInt(match[3], 16) & 1) === 1 };
}

function validateEvent(value: unknown): RumEvent {
  if (!isRecord(value)) {
    throw new Rejection("invalid_shape");
  }
  if (value.type === "action") {
    onlyKeys(value, ACTION_KEYS);
    if (typeof value.name !== "string" || !Object.hasOwn(RUM_ACTIONS, value.name)) {
      throw new Rejection("invalid_name");
    }
    const name = value.name as RumActionName;
    if (typeof value.outcome !== "string" || !(RUM_ACTIONS[name] as readonly string[]).includes(value.outcome)) {
      throw new Rejection("invalid_outcome");
    }
    const event: RumActionEvent = {
      type: "action",
      name,
      outcome: value.outcome,
      durationMs: finiteInRange(value.durationMs, 0, MAX_DURATION_MS),
      endedAgoMs: finiteInRange(value.endedAgoMs, 0, MAX_ENDED_AGO_MS),
      route: route(value.route),
    };
    if (value.httpStatus !== undefined) {
      const status = finiteInRange(value.httpStatus, 0, 599);
      if (!Number.isInteger(status)) throw new Rejection("invalid_number");
      event.httpStatus = status;
    }
    if (value.traceparent !== undefined) {
      if (typeof value.traceparent !== "string" || !parseTraceparent(value.traceparent)) {
        throw new Rejection("invalid_traceparent");
      }
      event.traceparent = value.traceparent;
    }
    return event;
  }
  if (value.type === "vital") {
    onlyKeys(value, VITAL_KEYS);
    if (typeof value.name !== "string" || !(RUM_WEB_VITALS as readonly string[]).includes(value.name)) {
      throw new Rejection("invalid_name");
    }
    if (typeof value.rating !== "string" || !RATINGS.has(value.rating)) {
      throw new Rejection("invalid_outcome");
    }
    const name = value.name as RumWebVitalName;
    return {
      type: "vital",
      name,
      value: finiteInRange(value.value, 0, name === "CLS" ? 100 : MAX_DURATION_MS),
      rating: value.rating as RumVitalEvent["rating"],
      route: route(value.route),
    };
  }
  throw new Rejection("invalid_shape");
}

/** Throws `Rejection("invalid_shape")` if the envelope itself is invalid. */
export function validateRumPayload(payload: unknown): RumValidationResult {
  if (!isRecord(payload) || payload.v !== RUM_SCHEMA_VERSION || !Array.isArray(payload.events)) {
    throw new Rejection("invalid_shape");
  }
  const result: RumValidationResult = { events: [], rejected: {} };
  const reject = (reason: RejectReason, count = 1) => {
    result.rejected[reason] = (result.rejected[reason] ?? 0) + count;
  };
  if (payload.events.length > RUM_MAX_EVENTS) {
    reject("too_many_events", payload.events.length - RUM_MAX_EVENTS);
  }
  for (const raw of payload.events.slice(0, RUM_MAX_EVENTS)) {
    try {
      result.events.push(validateEvent(raw));
    } catch (error) {
      reject(error instanceof Rejection ? error.reason : "invalid_shape");
    }
  }
  return result;
}

export function isRumRejection(error: unknown): boolean {
  return error instanceof Rejection;
}

const tracer = trace.getTracer(INSTRUMENTATION_SCOPE);

function recordActionSpan(event: RumActionEvent, receivedAtMs: number): void {
  const parent = event.traceparent ? parseTraceparent(event.traceparent) : null;
  if (!parent?.sampled) {
    return;
  }
  const endMs = receivedAtMs - event.endedAgoMs;
  const startMs = endMs - event.durationMs;
  const attributes: Attributes = {
    [ATTR.telemetrySource]: "browser",
    [ATTR.actionName]: event.name,
    [ATTR.actionOutcome]: event.outcome,
    [ATTR.pageRoute]: event.route,
    [ATTR.rumTimingBasis]: "server_receive_relative",
    ...(event.httpStatus !== undefined ? { "http.response.status_code": event.httpStatus } : {}),
  };
  withForcedSpanIds({ traceId: parent.traceId, spanId: parent.spanId }, () => {
    const span = tracer.startSpan(
      event.name === "translation" ? SPAN.rumTranslation : SPAN.rumSelectAll,
      { root: true, kind: SpanKind.INTERNAL, startTime: startMs, attributes },
      ROOT_CONTEXT.setValue(FORCE_SAMPLE_KEY, true),
    );
    if (event.outcome === "error") {
      span.setStatus({ code: SpanStatusCode.ERROR });
    }
    span.end(endMs);
  });
}

export function recordRumEvents(result: RumValidationResult, receivedAtMs = Date.now()): void {
  safeRecord(({ rumActionDuration, rumWebVitals, rumEventsRejected }) => {
    for (const [reason, count] of Object.entries(result.rejected)) {
      rumEventsRejected.add(count ?? 0, { reason });
    }
    for (const event of result.events) {
      if (event.type === "action") {
        rumActionDuration.record(event.durationMs / 1000, {
          [ATTR.actionName]: event.name,
          [ATTR.actionOutcome]: event.outcome,
          [ATTR.pageRoute]: event.route,
        });
      } else {
        rumWebVitals[event.name].record(event.name === "CLS" ? event.value : event.value / 1000, {
          [ATTR.pageRoute]: event.route,
          [ATTR.rumRating]: event.rating,
        });
      }
    }
  });
  for (const event of result.events) {
    if (event.type === "action") {
      try {
        recordActionSpan(event, receivedAtMs);
      } catch {
        // Telemetry must not affect request handling.
      }
    }
  }
}
