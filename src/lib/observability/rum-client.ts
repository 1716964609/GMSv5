/**
 * Browser RUM client (As-To-Be §5.1). Browser-only module; no Node APIs.
 *
 * Enabled at build time with NEXT_PUBLIC_GMS_RUM_ENABLED=true and sampled
 * per page load with NEXT_PUBLIC_GMS_RUM_SAMPLE_RATE (0–1, default 1).
 * When disabled every function is a cheap no-op and no `traceparent`
 * header is added to API requests.
 *
 * Privacy: only enum names / outcomes, page route templates, numbers and
 * W3C trace IDs are sent. Never text, terms, IDs, URLs or error messages.
 * Failures are swallowed: RUM must never break a user operation.
 */

import {
  normalizePageRoute,
  type RumActionName,
  type RumActionOutcome,
  type RumWebVitalName,
} from "./schema.ts";

const ENDPOINT = "/api/telemetry/rum";
const FLUSH_INTERVAL_MS = 10_000;
const MAX_BATCH = 20;
const MAX_QUEUE = 100;

type ActionPayload = {
  type: "action";
  name: RumActionName;
  outcome: string;
  durationMs: number;
  endedAgoMs: number;
  route: string;
  httpStatus?: number;
  traceparent?: string;
};

type VitalPayload = {
  type: "vital";
  name: RumWebVitalName;
  value: number;
  rating: "good" | "needs-improvement" | "poor";
  route: string;
};

type QueuedEvent =
  | (Omit<ActionPayload, "endedAgoMs"> & { endedAt: number })
  | VitalPayload;

export type UserAction<N extends RumActionName = RumActionName> = {
  /** Headers to merge into every API request belonging to this action. */
  headers(): Record<string, string>;
  /** End now. Idempotent: only the first call is recorded. */
  end(outcome: RumActionOutcome<N>, details?: { httpStatus?: number }): void;
  /** End after the next paint following the current render commit. */
  endAfterPaint(outcome: RumActionOutcome<N>, details?: { httpStatus?: number }): void;
};

function parseRate(value: string | undefined): number {
  const parsed = Number(value);
  return value === undefined || value === "" || !Number.isFinite(parsed) ? 1 : Math.min(1, Math.max(0, parsed));
}

const configuredEnabled = process.env.NEXT_PUBLIC_GMS_RUM_ENABLED === "true";
const sampleRate = parseRate(process.env.NEXT_PUBLIC_GMS_RUM_SAMPLE_RATE);

let sampledDecision: boolean | undefined;
const queue: QueuedEvent[] = [];
let timer: ReturnType<typeof setTimeout> | undefined;
let listenersInstalled = false;

function isBrowser(): boolean {
  return typeof window !== "undefined" && typeof performance !== "undefined";
}

export function isRumActive(): boolean {
  if (!configuredEnabled || !isBrowser()) {
    return false;
  }
  if (sampledDecision === undefined) {
    sampledDecision = Math.random() < sampleRate;
  }
  return sampledDecision;
}

function randomHex(bytes: number): string {
  const values = new Uint8Array(bytes);
  crypto.getRandomValues(values);
  let hex = "";
  for (const value of values) {
    hex += value.toString(16).padStart(2, "0");
  }
  return /^0+$/.test(hex) ? randomHex(bytes) : hex;
}

/** W3C Trace Context `traceparent`, version 00, sampled. */
export function createTraceparent(): string {
  return `00-${randomHex(16)}-${randomHex(8)}-01`;
}

function currentRoute(): string {
  try {
    return normalizePageRoute(window.location.pathname);
  } catch {
    return "other";
  }
}

function send(events: QueuedEvent[]): void {
  if (events.length === 0) {
    return;
  }
  const now = performance.now();
  const payload = {
    v: 1,
    events: events.map((event) => {
      if (event.type !== "action") {
        return event;
      }
      const { endedAt, ...rest } = event;
      return { ...rest, endedAgoMs: Math.max(0, Math.round(now - endedAt)) } satisfies ActionPayload;
    }),
  };
  try {
    void fetch(ENDPOINT, {
      method: "POST",
      keepalive: true,
      credentials: "same-origin",
      cache: "no-store",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    }).catch(() => undefined);
  } catch {
    // Ignore: telemetry is best effort.
  }
}

export function flushRum(): void {
  if (timer !== undefined) {
    clearTimeout(timer);
    timer = undefined;
  }
  while (queue.length > 0) {
    send(queue.splice(0, MAX_BATCH));
  }
}

function installListeners(): void {
  if (listenersInstalled) {
    return;
  }
  listenersInstalled = true;
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      flushRum();
    }
  });
  window.addEventListener("pagehide", flushRum);
}

function enqueue(event: QueuedEvent): void {
  try {
    installListeners();
    if (queue.length >= MAX_QUEUE) {
      queue.shift();
    }
    queue.push(event);
    if (queue.length >= MAX_BATCH) {
      flushRum();
    } else if (timer === undefined) {
      timer = setTimeout(flushRum, FLUSH_INTERVAL_MS);
    }
  } catch {
    // Ignore.
  }
}

const NOOP_ACTION: UserAction = {
  headers: () => ({}),
  end: () => undefined,
  endAfterPaint: () => undefined,
};

/**
 * Start measuring a user action. `startTime` defaults to now; pass the input
 * event's `timeStamp` to include input delay.
 */
export function startUserAction<N extends RumActionName>(name: N, startTime?: number): UserAction<N> {
  if (!isRumActive()) {
    return NOOP_ACTION as UserAction<N>;
  }
  try {
    const start =
      typeof startTime === "number" && Number.isFinite(startTime) && startTime > 0 && startTime <= performance.now()
        ? startTime
        : performance.now();
    const traceparent = createTraceparent();
    const route = currentRoute();
    let ended = false;
    const end: UserAction<N>["end"] = (outcome, details) => {
      if (ended) return;
      ended = true;
      const endedAt = performance.now();
      enqueue({
        type: "action",
        name,
        outcome,
        durationMs: Math.max(0, Math.round((endedAt - start) * 10) / 10),
        endedAt,
        route,
        traceparent,
        ...(details?.httpStatus !== undefined ? { httpStatus: details.httpStatus } : {}),
      });
    };
    return {
      headers: () => ({ traceparent }),
      end,
      endAfterPaint: (outcome, details) => {
        try {
          // rAF runs before the next paint; the timeout runs after it.
          requestAnimationFrame(() => setTimeout(() => end(outcome, details), 0));
        } catch {
          end(outcome, details);
        }
      },
    };
  } catch {
    return NOOP_ACTION as UserAction<N>;
  }
}

export function reportWebVital(name: RumWebVitalName, value: number, rating: VitalPayload["rating"]): void {
  if (!isRumActive() || !Number.isFinite(value) || value < 0) {
    return;
  }
  enqueue({
    type: "vital",
    name,
    value: name === "CLS" ? Math.round(value * 10000) / 10000 : Math.round(value),
    rating,
    route: currentRoute(),
  });
}
