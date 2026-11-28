"""Responder context for burn rate notifications: "Now" and the last 60 minutes.

Two read-only lookups, made only when a notification is about to be sent
(or, in shadow mode, logged):

Now     The latest scheduled HTTP check, from the existing HTTP state row
        (instance_id = "http#sunlightjetrans.com", event_time = "~STATE"),
        read when the message is generated, NOT at the alarm transition.
Last 60 HTTPUp (Minimum, 60 s) for the 60 completed minutes before the alarm
        state timestamp, [floor(state_at) - 60 min, floor(state_at)), from
        CloudWatch GetMetricData. This is an independent wall-clock window, not
        CloudWatch's internal evaluation range (about 66 minutes observed with
        missing data, Availability Runbook §13.2).

Both are best effort. A failure, a timeout or an exhausted time budget only
replaces that line with "unavailable"; the notification is still sent.

Time bound. SDK timeouts are per socket operation, so they do not bound a
call. Each lookup therefore runs in a daemon thread and is waited for only
until the invocation's lookup deadline: 30 s after the Context is created,
and never later than 25 s before the Lambda timeout. A lookup still running
at the deadline is abandoned (its result is discarded).

Threads. The low-level clients are created on the calling (main) thread,
once per execution environment, and only when a lookup is about to run;
workers only call a read API on a client they are handed and never touch a
boto3 Session. At most MAX_WORKERS workers exist per execution environment,
counting abandoned ones: a worker slot is taken before the thread starts
and released only when the worker itself ends. With no free slot the
lookup is skipped ("busy") without waiting.
The metric, dimension and region are fixed here; nothing from the event
selects what is read. No burn rate, availability or error budget is computed.
"""
from __future__ import annotations

import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache

REGION = "ap-northeast-1"
NAMESPACE = "GMS/Availability"
METRIC = "HTTPUp"
TARGET = "sunlightjetrans.com"
PERIOD = 60
STAT = "Minimum"
THRESHOLD = 1.0           # Same comparison as the alarm: failed when < 1.
WINDOW_MINUTES = 60
QUERY_ID = "httpup"

HTTP_KEY = {"instance_id": "http#" + TARGET, "event_time": "~STATE"}
# Probes run every minute. An older last check is not a current state.
STALE_SECONDS = 180
CONFIRM_FAILURES = 2      # handler.track_http_status marks DOWN at the 2nd failure.

# SDK limits: they keep a single attempt short and stop retry storms, but
# are per socket operation, so the hard bound is the deadline below.
CONNECT_TIMEOUT, READ_TIMEOUT, TOTAL_ATTEMPTS = 2, 3, 2
# Hard limits (wall clock, enforced by Context.call):
BUDGET_SECONDS = 30          # All context lookups of one invocation.
NOTIFY_RESERVE_SECONDS = 25  # Lambda time the lookups never eat into.
MIN_START_SECONDS = 2        # Do not start a lookup with less time than this.
MAX_PAGES = 2                # 60 datapoints fit in one page; NextToken is unexpected.
MAX_WORKERS = 2              # Lookup threads per execution environment, incl. abandoned.

WORKER_SLOTS = threading.BoundedSemaphore(MAX_WORKERS)
_ACTIVE_LOCK = threading.Lock()
_ACTIVE = [0]


def active_workers() -> int:
    """Workers currently running (including abandoned ones). For logs and tests."""
    with _ACTIVE_LOCK:
        return _ACTIVE[0]


def _count(delta: int):
    with _ACTIVE_LOCK:
        _ACTIVE[0] += delta

JST = timezone(timedelta(hours=9), "JST")


class BudgetExhausted(Exception):
    """Not enough time left for another worst-case call."""


class QueryFailed(Exception):
    """GetMetricData answered, but not with usable data. str() is a short code."""


class DeadlineExceeded(Exception):
    """The lookup was still running at the deadline and was abandoned."""


class WorkersBusy(Exception):
    """MAX_WORKERS lookup threads are still running; not waiting for one."""


def bounded(fn, timeout: float):
    """Run fn() in a daemon thread and wait at most timeout seconds (wall clock).

    fn must only call a read API on a client created by the caller. Its result
    goes to a box owned by this call alone, so a late result is discarded.
    """
    if timeout < MIN_START_SECONDS:
        raise BudgetExhausted()
    if not WORKER_SLOTS.acquire(blocking=False):
        raise WorkersBusy()
    _count(+1)
    box = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller.
            box["error"] = exc
        finally:
            # Released when the worker ends, not when the caller stops waiting.
            _count(-1)
            WORKER_SLOTS.release()

    worker = threading.Thread(target=run, name="burn-rate-context", daemon=True)
    try:
        worker.start()
    except BaseException:
        _count(-1)
        WORKER_SLOTS.release()
        raise
    worker.join(timeout)
    if worker.is_alive():
        raise DeadlineExceeded()
    if "error" in box:
        raise box["error"]
    return box["value"]


def direct(fn):
    return fn()


def sdk_config():
    from botocore.config import Config

    return Config(connect_timeout=CONNECT_TIMEOUT, read_timeout=READ_TIMEOUT,
                  retries={"mode": "standard", "total_max_attempts": TOTAL_ATTEMPTS})


@lru_cache(maxsize=1)
def cloudwatch():
    import boto3

    return boto3.client("cloudwatch", region_name=REGION, config=sdk_config())


@lru_cache(maxsize=1)
def dynamodb():
    """Own bounded low-level client (thread safe, unlike boto3 resources)."""
    import boto3

    return boto3.client("dynamodb", config=sdk_config())


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def jst(moment: datetime) -> str:
    return moment.astimezone(JST).strftime("%H:%M JST")


def window(state_at: str):
    """[start, end): end = the alarm state timestamp rounded down to the minute."""
    end = utc(state_at).replace(second=0, microsecond=0)
    return end - timedelta(minutes=WINDOW_MINUTES), end


# ---------------------------------------------------------------- Now ----

def slot_time(value) -> datetime | None:
    """last_slot is the scheduled minute, "YYYY-MM-DDTHH:MM" (UTC)."""
    if not isinstance(value, str) or len(value) != 16:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def row_time(value) -> datetime | None:
    """first_fail_at / down_since: "YYYY-MM-DDTHH:MM:SSZ", when the check was recorded."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def failures(row: dict) -> int | None:
    try:
        count = int(row.get("http_failures", 0))
    except (TypeError, ValueError):
        return None
    return count if count >= 0 else None


def http_now(row: dict, now: datetime) -> dict:
    """Classify the latest check. Never UP unless it is fresh and passed."""
    last = slot_time(row.get("last_slot"))
    count = failures(row)
    status = row.get("http_status")
    if last is None or count is None:
        return {"state": "UNKNOWN", "why": "no HTTP check recorded"}
    age = (now - last).total_seconds()
    if age >= STALE_SECONDS or age < -PERIOD:
        known = "DOWN" if status == "down" else ("failed" if count else "passed")
        return {"state": "UNKNOWN", "why": "stale", "last": last, "last_result": known}
    first = row_time(row.get("down_since")) or row_time(row.get("first_fail_at"))
    if status == "down":
        return {"state": "DOWN", "last": last, "first_fail": first, "count": count}
    if count >= 1:
        return {"state": "FAILING", "last": last, "first_fail": first, "count": count}
    if status == "up":
        return {"state": "UP", "last": last}
    return {"state": "UNKNOWN", "why": "unrecognized HTTP state"}


def now_line(info: dict, now: datetime) -> str:
    state = info["state"]
    if state == "UP":
        return f"Now: UP (last check {jst(info['last'])})"
    if state in ("DOWN", "FAILING"):
        first, count = info.get("first_fail"), info.get("count", 0)
        checks = f"{count} consecutive failed check{'s' if count != 1 else ''}"
        if first is None:
            since = "since: unknown"
        else:
            minutes = max(0, int((now - first).total_seconds() // 60))
            since = f"{minutes} min (since first failed check {jst(first)})"
        if state == "DOWN":
            return f"Now: DOWN — {since}; {checks}"
        return (f"Now: FAILING — {since}; {checks}, "
                f"DOWN not confirmed ({CONFIRM_FAILURES} needed)")
    if info.get("why") == "stale":
        return (f"Now: UNKNOWN — no HTTP check since {jst(info['last'])} "
                f"(last result: {info['last_result']})")
    return f"Now: UNKNOWN — {info.get('why', 'HTTP state unavailable')}"


def deserializer():
    from boto3.dynamodb.types import TypeDeserializer

    return TypeDeserializer()


def fetch_http_row(client, table_name: str, types=None) -> dict:
    types = types or deserializer()
    response = client.get_item(
        TableName=table_name, ConsistentRead=True,
        Key={name: {"S": value} for name, value in HTTP_KEY.items()})
    return {k: types.deserialize(v) for k, v in (response.get("Item") or {}).items()}


# ------------------------------------------------------ Last 60 minutes ----

def query():
    return [{
        "Id": QUERY_ID,
        "MetricStat": {
            "Metric": {"Namespace": NAMESPACE, "MetricName": METRIC,
                       "Dimensions": [{"Name": "Target", "Value": TARGET}]},
            "Period": PERIOD,
            "Stat": STAT,
            # No Unit: like the alarm, a unit filter could hide datapoints.
        },
        "ReturnData": True,
    }]


def fetch_metric(client, start: datetime, end: datetime, call=direct) -> dict:
    """GetMetricData for the fixed query. call(fn) runs each page under the deadline."""
    timestamps, values, complete, token = [], [], False, None
    for number in range(MAX_PAGES):
        kwargs = {"MetricDataQueries": query(), "StartTime": start, "EndTime": end,
                  "ScanBy": "TimestampAscending"}
        if token:
            kwargs["NextToken"] = token
        try:
            page = call(lambda: client.get_metric_data(**kwargs))
        except Exception:  # noqa: BLE001 - a later page only makes the result partial.
            if number == 0:
                raise
            break  # Keep the pages already read; reported as partial.
        result = next((r for r in page.get("MetricDataResults", [])
                       if r.get("Id") == QUERY_ID), None)
        if result is None:
            raise QueryFailed("no result for the query")
        code = result.get("StatusCode")
        if code in ("Forbidden", "InternalError"):
            raise QueryFailed(code)
        page_times, page_values = result.get("Timestamps"), result.get("Values")
        if (not isinstance(page_times, list) or not isinstance(page_values, list)
                or len(page_times) != len(page_values)):
            raise QueryFailed("malformed result")
        timestamps += page_times
        values += page_values
        token = page.get("NextToken")
        if code == "Complete" and not token:
            complete = True
            break
        if not token:
            break  # PartialData without a token: nothing more to ask for.
    return {"timestamps": timestamps, "values": values, "complete": complete}


def summarize(timestamps, values, start: datetime, end: datetime, complete=True) -> dict:
    """Classify each of the 60 minute slots as failed, ok or missing.

    Order does not matter (slots are keyed by timestamp). Timestamps outside
    [start, end) or not on a minute boundary, and non-numeric or non-finite
    values, are ignored and leave the slot missing. Duplicates for one slot
    keep the minimum, as the Minimum statistic would.
    """
    slots = [start + timedelta(minutes=i) for i in range(WINDOW_MINUTES)]
    index = {slot: i for i, slot in enumerate(slots)}
    best: dict[int, float] = {}
    ignored = 0
    for ts, value in zip(timestamps, values):
        if (not isinstance(ts, datetime) or ts.tzinfo is None
                or isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value)):
            ignored += 1
            continue
        position = index.get(ts.astimezone(timezone.utc))
        if position is None:
            ignored += 1
            continue
        best[position] = min(value, best.get(position, value))
    marks = ["missing"] * WINDOW_MINUTES
    for position, value in best.items():
        marks[position] = "failed" if value < THRESHOLD else "ok"
    episodes, run, first_cut, last_cut = [], 0, False, False
    for position, mark in enumerate(marks + ["end"]):
        if mark == "failed":
            run += 1
            continue
        if run:
            episodes.append(run)
            first_cut = first_cut or position - run == 0
            last_cut = last_cut or position == WINDOW_MINUTES
        run = 0
    return {
        "start": start, "end": end,
        "failed": marks.count("failed"), "ok": marks.count("ok"),
        "missing": marks.count("missing"),
        "episodes": len(episodes), "longest": max(episodes, default=0),
        "cut_at_start": first_cut, "cut_at_end": last_cut,
        "ignored": ignored, "complete": bool(complete),
    }


def window_lines(summary: dict) -> list[str]:
    span = f"{jst(summary['start'])}–{jst(summary['end'])}"
    counts = (f"Last 60 completed min ({span}): failed {summary['failed']} / "
              f"ok {summary['ok']} / missing {summary['missing']}")
    notes = []
    if not summary["complete"]:
        notes.append("partial API response")
    if summary["ignored"]:
        notes.append(f"{summary['ignored']} unusable datapoints ignored")
    if notes:
        counts += f" ({'; '.join(notes)})"
    if not summary["failed"]:
        return [counts, "Pattern: no failed minutes"]
    plural = "s" if summary["episodes"] != 1 else ""
    pattern = (f"Pattern: {summary['episodes']} observed episode{plural}, "
               f"longest {summary['longest']} min")
    cut = [edge for edge, flag in (("start", summary["cut_at_start"]),
                                   ("end", summary["cut_at_end"])) if flag]
    if cut:
        pattern += f" (an episode at the window {' and '.join(cut)} is counted only inside it)"
    if summary["missing"]:
        pattern += "; missing minutes split episodes"
    return [counts, pattern]


def error_name(exc: Exception) -> str:
    """A short code for the message. Never the exception text from AWS."""
    if isinstance(exc, BudgetExhausted):
        return "time budget"
    if isinstance(exc, DeadlineExceeded):
        return "deadline"
    if isinstance(exc, WorkersBusy):
        return "busy"
    if isinstance(exc, QueryFailed):
        return str(exc)
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = (response.get("Error") or {}).get("Code")
        if isinstance(code, str) and code:
            return code[:64]
    return type(exc).__name__


# ------------------------------------------------------------ Context ----

class Context:
    """Per-invocation lookups under one hard deadline.

    http_row():                 the HTTP state row (raises on failure)
    metrics(start, end, call):  fetch_metric() result; call(fn) bounds each call
    now():                      wall clock, UTC
    lambda_remaining():         optional, seconds left in the Lambda invocation

    prepare():                  optional; returns (http_row, metrics) bound to clients.
                                Called once, on the calling thread, before the
                                first lookup; never at construction.

    Deadline = min(creation + budget_seconds, Lambda end - NOTIFY_RESERVE_SECONDS),
    fixed when the Context is created. Client creation in prepare() counts
    against it but cannot be interrupted.
    """

    def __init__(self, *, now, http_row=None, metrics=None, prepare=None,
                 lambda_remaining=None, clock=time.monotonic, log=None,
                 budget_seconds=BUDGET_SECONDS):
        self.http_row, self.metrics, self.now = http_row, metrics, now
        self.prepare, self.prepared, self.prepare_error = prepare, prepare is None, None
        self.clock, self.log = clock, log
        start = clock()
        self.deadline = start + budget_seconds
        if lambda_remaining is not None:
            self.deadline = min(self.deadline,
                                start + lambda_remaining() - NOTIFY_RESERVE_SECONDS)
        self.calls = {"http_row": 0, "metrics": 0}

    def remaining(self) -> float:
        return self.deadline - self.clock()

    def call(self, fn):
        return bounded(fn, self.remaining())

    def lookups(self):
        """(http_row, metrics). Clients are created here, on the calling thread."""
        if not self.prepared:
            self.prepared = True
            try:
                if self.remaining() < MIN_START_SECONDS:
                    raise BudgetExhausted()
                self.http_row, self.metrics = self.prepare()
            except Exception as exc:  # noqa: BLE001 - reported per line below.
                self.prepare_error = exc
        if self.prepare_error is not None:
            raise self.prepare_error
        return self.http_row, self.metrics

    def warn(self, part: str, exc: Exception):
        if self.log is not None:
            self.log.warning("Burn rate context %s unavailable: %s", part, error_name(exc))

    def now_text(self) -> str:
        try:
            http_row, _ = self.lookups()
            self.calls["http_row"] += 1
            row = self.call(http_row)
            moment = self.now()
            return now_line(http_now(row if isinstance(row, dict) else {}, moment), moment)
        except Exception as exc:  # noqa: BLE001 - context never blocks a notification.
            self.warn("now", exc)
            return f"Now: UNKNOWN — HTTP state unavailable ({error_name(exc)})"

    def window_text(self, state_at: str) -> list[str]:
        try:
            start, end = window(state_at)
            _, metrics = self.lookups()
            self.calls["metrics"] += 1
            data = metrics(start, end, self.call)
            return window_lines(summarize(data["timestamps"], data["values"], start, end,
                                          data["complete"]))
        except Exception as exc:  # noqa: BLE001 - context never blocks a notification.
            self.warn("last60", exc)
            return [f"Last 60 completed min: unavailable ({error_name(exc)})"]

    def lines(self, state_at: str) -> list[str]:
        return [self.now_text()] + self.window_text(state_at)


def prepare_production():
    """Create (or reuse) both clients on the calling thread; bind them to the lookups."""
    ddb, cw, name, types = dynamodb(), cloudwatch(), os.environ["TABLE_NAME"], deserializer()
    return (lambda: fetch_http_row(ddb, name, types),
            lambda start, end, call: fetch_metric(cw, start, end, call))


def production(now, lambda_remaining=None, log=None) -> Context:
    """The Lambda wiring: bounded low-level clients, fixed query. Creates no client."""
    return Context(prepare=prepare_production, now=now,
                   lambda_remaining=lambda_remaining, log=log)
