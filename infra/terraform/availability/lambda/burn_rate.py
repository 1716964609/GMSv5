"""Availability burn rate alarm notifications (CloudWatch Alarm State Change).

CloudWatch evaluates the alarm natively; this module only turns its state
changes into Discord messages. It never reads metrics and writes DynamoDB
only on alarm state changes, never per minute.

One state row per alarm, separate from EC2, HTTP, deployment and Spot rows:
  instance_id = "burnrate#<alarm name>", event_time = "~STATE"

Events update the row only when newer (detail.state.timestamp), so duplicate
and out-of-order deliveries never overwrite a newer state. Every invocation
then reconciles: the notification owed for the CURRENT state is derived from
the row, not from the event that triggered the invocation.

Delivery is at least once, best effort:
  claim (lease) -> notify() -> mark notified.
A notification is marked delivered only after notify() returns. If notify()
fails, the lease is released and the exception propagates so that Lambda's
asynchronous retry tries again. Residual cases are documented in the
Availability Runbook §13.
"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone

from botocore.exceptions import ClientError

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)

SOURCE = "aws.cloudwatch"
DETAIL_TYPE = "CloudWatch Alarm State Change"
STATES = {"ALARM", "OK", "INSUFFICIENT_DATA"}
STATE_KEY = "~STATE"

# Descriptive values for messages. They must match burn_rate.tf; a test
# compares them. The alarm itself is configured only in Terraform.
TARGET = "sunlightjetrans.com"
SLO = 0.99
SLO_WINDOW = "rolling 7 days"
ERROR_BUDGET_MINUTES = 100.8
EVALUATION_PERIODS = 60
DATAPOINTS_TO_ALARM = 9
DESIGN_BURN_RATE = 14.4

# No invocation outlives the 90 s Lambda timeout, so an older lease is dead.
LEASE_SECONDS = 90
MAX_RECONCILE_ROUNDS = 3

TIMESTAMP = re.compile(
    r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d{1,6})?(Z|[+-]\d\d:?\d\d)$")


class NotificationInFlight(RuntimeError):
    """Another invocation holds the delivery lease. Raised so Lambda retries."""


def state_time(value) -> str | None:
    """Normalize a CloudWatch timestamp to a sortable UTC string (ms)."""
    if not isinstance(value, str):
        return None
    match = TIMESTAMP.match(value.strip())
    if not match:
        return None
    base, fraction, zone = match.groups()
    zone = "+00:00" if zone == "Z" else zone
    if ":" not in zone:
        zone = zone[:3] + ":" + zone[3:]
    try:
        moment = datetime.fromisoformat(base + (fraction or "") + zone)
    except ValueError:
        return None
    moment = moment.astimezone(timezone.utc)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def observed(state: dict) -> dict:
    """Failed samples as listed in reasonData. Never inferred from the threshold."""
    try:
        data = json.loads(state.get("reasonData") or "")
        values = data["recentDatapoints"]
        threshold = data["threshold"]
        if (not isinstance(values, list) or not values
                or isinstance(threshold, bool) or not isinstance(threshold, (int, float))
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                           and math.isfinite(v) for v in values)):
            return {}
    except (TypeError, ValueError, KeyError):
        return {}
    return {"listed": len(values), "failed": sum(1 for v in values if v < threshold)}


def parse(event: dict, *, alarm_name: str, alarm_arn: str):
    """Return the accepted state change, or a reason string for ignoring it."""
    if event.get("source") != SOURCE or event.get("detail-type") != DETAIL_TYPE:
        return "unsupported event"
    detail = event.get("detail") or {}
    if not alarm_name or detail.get("alarmName") != alarm_name:
        return "unmonitored alarm"
    if not alarm_arn or event.get("resources") != [alarm_arn]:
        return "unmonitored alarm ARN"
    state = detail.get("state") or {}
    previous = detail.get("previousState") or {}
    value = state.get("value")
    if value not in STATES:
        return "unsupported alarm state"
    at = state_time(state.get("timestamp")) or state_time(event.get("time"))
    if not at:
        return "missing state timestamp"
    reason = state.get("reason") if isinstance(state.get("reason"), str) else ""
    change = {
        "alarm_state": value,
        "state_at": at,
        "previous_state": previous.get("value") if previous.get("value") in STATES else "UNKNOWN",
        "event_id": str(event.get("id", "no-id")),
        # CloudWatch uses "Unchecked: ..." for state set without an evaluation.
        "reason_kind": "unchecked" if reason.startswith("Unchecked") else "evaluated",
    }
    change.update({f"observed_{k}": v for k, v in observed(state).items()})
    return change


def owed(item: dict) -> str | None:
    """Which notification the current state still needs, given the last delivered one."""
    current, delivered = item.get("alarm_state"), item.get("notified_state")
    if current == "ALARM":
        return "alarm" if delivered != "ALARM" else None
    if current == "OK":
        if delivered == "ALARM":
            return "normalized"
        if delivered == "INSUFFICIENT_DATA":
            return "data_restored"
        return None  # Includes the initial INSUFFICIENT_DATA -> OK evaluation.
    if current == "INSUFFICIENT_DATA":
        if item.get("reason_kind") == "unchecked":
            return None  # Alarm created or reset; not a monitoring gap.
        return "insufficient" if delivered != "INSUFFICIENT_DATA" else None
    return None


def observation_line(item: dict) -> str:
    listed, failed = item.get("observed_listed"), item.get("observed_failed")
    if listed is None or failed is None:
        return "Observed: not reported in the alarm event"
    listed, failed = int(listed), int(failed)
    if listed == EVALUATION_PERIODS:
        rate = failed / EVALUATION_PERIODS / (1 - SLO)
        return (f"Observed: {failed} of {listed} one-minute samples failed "
                f"= {rate:.1f}x burn rate (alarm reasonData)")
    return (f"Observed: {failed} of {listed} datapoints listed in the alarm event were "
            f"below the threshold; not a full {EVALUATION_PERIODS}-sample window, "
            f"so no burn rate is computed")


def message(kind: str, item: dict, alarm_name: str) -> str:
    full = DATAPOINTS_TO_ALARM / EVALUATION_PERIODS / (1 - SLO)
    condition = (f"Condition (configured): at least {DATAPOINTS_TO_ALARM} of the last "
                 f"{EVALUATION_PERIODS} one-minute HTTPUp samples failed "
                 f"(= {full:.1f}x burn rate on a full window; design threshold "
                 f"{DESIGN_BURN_RATE}x)")
    header = {
        "alarm": "[HIGH] AVAILABILITY BURN RATE ALARM",
        "normalized": "[HIGH] BURN RATE NORMALIZED",
        "insufficient": "[WARN] BURN RATE DATA INSUFFICIENT",
        "data_restored": "[INFO] BURN RATE DATA RESTORED",
    }[kind]
    meaning = {
        "alarm": "The error budget is being consumed fast. Check HTTP status and EC2 "
                 "or Spot notifications.",
        "normalized": "The alarm condition is no longer met. This does NOT confirm that "
                      "the site is up now; HTTP DOWN/RECOVERED notifications show "
                      "the current state.",
        "insufficient": "The alarm has no HTTPUp datapoints to evaluate. This is a "
                        "monitoring data problem, not proof of downtime or recovery. "
                        "Check the scheduled HTTP probe (Lambda gmsv5-availability).",
        "data_restored": "The alarm has datapoints again and its condition is not met. "
                         "This does NOT confirm that the site is up now.",
    }[kind]
    lines = [
        header,
        f"Target: {TARGET} (external HTTP probe, metric HTTPUp)",
        f"SLO: {SLO:.0%} availability, {SLO_WINDOW} "
        f"(error budget {ERROR_BUDGET_MINUTES} min)",
        condition,
        observation_line(item),
        f"Transition: {item.get('previous_state', 'UNKNOWN')} -> {item['alarm_state']} "
        f"at {item['state_at']} (alarm state timestamp, UTC)",
        f"Alarm: {alarm_name}",
        meaning,
    ]
    return "\n".join(lines)


def is_conditional(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def row_key(alarm_name: str) -> dict:
    return {"instance_id": "burnrate#" + alarm_name, "event_time": STATE_KEY}


def accept(table, key: dict, change: dict, observed_at: str) -> str:
    """Store the change if it is newer than the stored state. Returns the outcome."""
    fields = dict(change, updated_at=observed_at)
    removable = [k for k in ("observed_listed", "observed_failed") if k not in fields]
    names = {f"#{k}": k for k in list(fields) + removable}
    expression = "SET " + ", ".join(f"#{k} = :{k}" for k in fields)
    if removable:
        expression += " REMOVE " + ", ".join(f"#{k}" for k in removable)
    try:
        table.update_item(
            Key=key,
            UpdateExpression=expression,
            ConditionExpression="attribute_not_exists(#state_at) OR #state_at < :state_at",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues={f":{k}": v for k, v in fields.items()},
        )
        return "accepted"
    except ClientError as exc:
        if not is_conditional(exc):
            raise
    return "not_newer"


def current(table, key: dict) -> dict:
    return table.get_item(Key=key, ConsistentRead=True).get("Item", {})


def claim(table, key: dict, at: str, now: int) -> bool:
    try:
        table.update_item(
            Key=key,
            UpdateExpression="SET #lease_at = :at, #lease_until = :until",
            ConditionExpression=(
                "#state_at = :at AND (attribute_not_exists(#notified_at) OR #notified_at <> :at) "
                "AND (attribute_not_exists(#lease_until) OR #lease_until < :now)"),
            ExpressionAttributeNames={"#state_at": "state_at", "#notified_at": "notified_at",
                                      "#lease_at": "lease_at", "#lease_until": "lease_until"},
            ExpressionAttributeValues={":at": at, ":until": now + LEASE_SECONDS, ":now": now},
        )
        return True
    except ClientError as exc:
        if not is_conditional(exc):
            raise
    return False


def release(table, key: dict, at: str):
    """Best effort. If it fails, the lease simply expires."""
    try:
        table.update_item(
            Key=key,
            UpdateExpression="REMOVE #lease_at, #lease_until",
            ConditionExpression="#lease_at = :at",
            ExpressionAttributeNames={"#lease_at": "lease_at", "#lease_until": "lease_until"},
            ExpressionAttributeValues={":at": at},
        )
    except Exception as exc:  # noqa: BLE001 - must not hide the notify error.
        LOG.warning("Burn rate lease release failed: %s", type(exc).__name__)


def mark(table, key: dict, at: str, state: str):
    """Record what was delivered, even if a newer state arrived meanwhile."""
    table.update_item(
        Key=key,
        UpdateExpression=("SET #notified_state = :state, #notified_at = :at "
                          "REMOVE #lease_at, #lease_until"),
        ConditionExpression="#lease_at = :at",
        ExpressionAttributeNames={"#notified_state": "notified_state",
                                  "#notified_at": "notified_at",
                                  "#lease_at": "lease_at", "#lease_until": "lease_until"},
        ExpressionAttributeValues={":state": state, ":at": at},
    )


def reconcile(table, key: dict, *, notify, now: int, deliver: bool, alarm_name: str):
    """Send what the current state is owed. Loops when the state moved during a send."""
    sent = []
    for _ in range(MAX_RECONCILE_ROUNDS):
        item = current(table, key)
        if not item:
            break
        lease = item.get("lease_until")
        if lease is not None and int(lease) >= now:
            # Another invocation is sending. Its outcome decides what is owed.
            raise NotificationInFlight(f"lease held for {item.get('lease_at')}")
        kind = owed(item)
        if kind is None:
            break
        at = item["state_at"]
        if not deliver:
            LOG.info(json.dumps({"burn_rate": "shadow", "would_send": kind, "state_at": at}))
            sent.append("shadow:" + kind)
            break
        if not claim(table, key, at, now):
            continue  # State, delivery or lease changed. Re-read and decide again.
        try:
            notify(message(kind, item, alarm_name))
        except Exception:
            release(table, key, at)
            LOG.error(json.dumps({"burn_rate": "notify_failed", "kind": kind, "state_at": at}))
            raise
        mark(table, key, at, item["alarm_state"])
        LOG.info(json.dumps({"burn_rate": "notified", "kind": kind, "state_at": at}))
        sent.append(kind)
    return sent


def handle(event: dict, *, table, notify, now, alarm_name: str, alarm_arn: str,
           delivery: str):
    change = parse(event, alarm_name=alarm_name, alarm_arn=alarm_arn)
    if isinstance(change, str):
        return {"ignored": change}
    key = row_key(alarm_name)
    moment = now()
    outcome = accept(table, key, change, moment.strftime("%Y-%m-%dT%H:%M:%SZ"))
    if outcome == "not_newer":
        stored = current(table, key).get("state_at", "")
        outcome = "duplicate" if stored == change["state_at"] else "stale"
    LOG.info(json.dumps({"burn_rate": outcome, "alarm_state": change["alarm_state"],
                         "state_at": change["state_at"]}))
    # Duplicates and stale events still reconcile: a retry may complete a failed send.
    sent = reconcile(table, key, notify=notify, now=int(moment.timestamp()),
                     deliver=delivery == "enabled", alarm_name=alarm_name)
    return {"burn_rate": outcome, "alarm_state": change["alarm_state"], "sent": sent}
