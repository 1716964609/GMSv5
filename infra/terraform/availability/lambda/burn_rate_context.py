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
The metric, dimension and region are fixed here; nothing from the event
selects what is read. No burn rate, availability or error budget is computed.
"""
from __future__ import annotations

import math
import os
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

# Time limits. Each AWS call: 2 s connect + 3 s read, at most 2 attempts.
CONNECT_TIMEOUT, READ_TIMEOUT, TOTAL_ATTEMPTS = 2, 3, 2
CALL_WORST_SECONDS = 13   # 2 x (2 + 3) s plus standard-mode backoff.
BUDGET_SECONDS = 30       # All context lookups of one invocation.
NOTIFY_RESERVE_SECONDS = 25  # Lambda time kept for notify() and the mark write.
MAX_PAGES = 2             # 60 datapoints fit in one page; NextToken is unexpected.

JST = timezone(timedelta(hours=9), "JST")


class BudgetExhausted(Exception):
    """Not enough time left for another worst-case call."""


class QueryFailed(Exception):
    """GetMetricData answered, but not with usable data. str() is a short code."""


def sdk_config():
    from botocore.config import Config

    return Config(connect_timeout=CONNECT_TIMEOUT, read_timeout=READ_TIMEOUT,
                  retries={"mode": "standard", "total_max_attempts": TOTAL_ATTEMPTS})


@lru_cache(maxsize=1)
def cloudwatch():
    import boto3

    return boto3.client("cloudwatch", region_name=REGION, config=sdk_config())


@lru_cache(maxsize=1)
def table():
    """Own bounded client; the handler's table() uses SDK defaults."""
    import boto3

    return boto3.resource("dynamodb", config=sdk_config()).Table(os.environ["TABLE_NAME"])


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


def fetch_http_row(source) -> dict:
    return source.get_item(Key=HTTP_KEY, ConsistentRead=True).get("Item", {})


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


def fetch_metric(client, start: datetime, end: datetime, remaining) -> dict:
    """GetMetricData for the fixed query. remaining() = seconds left in the budget."""
    timestamps, values, complete, token = [], [], False, None
    for _ in range(MAX_PAGES):
        if remaining() < CALL_WORST_SECONDS:
            if not timestamps and token is None:
                raise BudgetExhausted()
            break
        kwargs = {"MetricDataQueries": query(), "StartTime": start, "EndTime": end,
                  "ScanBy": "TimestampAscending"}
        if token:
            kwargs["NextToken"] = token
        page = client.get_metric_data(**kwargs)
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
    """Per-invocation lookups with one shared time budget.

    http_row():                 the HTTP state row (raises on failure)
    metrics(start, end, left):  fetch_metric() result (raises on failure)
    now():                      wall clock, UTC
    lambda_remaining():         optional, seconds left in the Lambda invocation
    """

    def __init__(self, *, http_row, metrics, now, lambda_remaining=None,
                 clock=time.monotonic, log=None):
        self.http_row, self.metrics, self.now = http_row, metrics, now
        self.lambda_remaining, self.clock, self.log = lambda_remaining, clock, log
        self.deadline = clock() + BUDGET_SECONDS
        self.calls = {"http_row": 0, "metrics": 0}

    def remaining(self) -> float:
        left = self.deadline - self.clock()
        if self.lambda_remaining is not None:
            left = min(left, self.lambda_remaining() - NOTIFY_RESERVE_SECONDS)
        return left

    def warn(self, part: str, exc: Exception):
        if self.log is not None:
            self.log.warning("Burn rate context %s unavailable: %s", part, error_name(exc))

    def now_text(self) -> str:
        try:
            if self.remaining() < CALL_WORST_SECONDS:
                raise BudgetExhausted()
            self.calls["http_row"] += 1
            row = self.http_row()
            moment = self.now()
            return now_line(http_now(row if isinstance(row, dict) else {}, moment), moment)
        except Exception as exc:  # noqa: BLE001 - context never blocks a notification.
            self.warn("now", exc)
            return f"Now: UNKNOWN — HTTP state unavailable ({error_name(exc)})"

    def window_text(self, state_at: str) -> list[str]:
        try:
            start, end = window(state_at)
            self.calls["metrics"] += 1
            data = self.metrics(start, end, self.remaining)
            return window_lines(summarize(data["timestamps"], data["values"], start, end,
                                          data["complete"]))
        except Exception as exc:  # noqa: BLE001 - context never blocks a notification.
            self.warn("last60", exc)
            return [f"Last 60 completed min: unavailable ({error_name(exc)})"]

    def lines(self, state_at: str) -> list[str]:
        return [self.now_text()] + self.window_text(state_at)


def production(now, lambda_remaining=None, log=None) -> Context:
    """The Lambda wiring: bounded clients, fixed query."""
    return Context(
        http_row=lambda: fetch_http_row(table()),
        metrics=lambda start, end, left: fetch_metric(cloudwatch(), start, end, left),
        now=now, lambda_remaining=lambda_remaining, log=log)
