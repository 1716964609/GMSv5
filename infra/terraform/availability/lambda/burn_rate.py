"""Availability burn rate alarm notifications (CloudWatch Alarm State Change).

CloudWatch evaluates the alarm natively; this module only turns its state
changes into Discord messages. It writes DynamoDB only on alarm state
changes, never per minute. Responder context (current HTTP state and the last
60 minutes of HTTPUp) is read by burn_rate_context only when a message is
about to be sent or, in shadow mode, logged.

Two alarms use this module, each described by a Profile (message text and
row prefix): the 9-of-60 burn rate alarm and the 6-of-6 continuous failure
alarm. One state row per alarm, separate from EC2, HTTP, deployment and Spot
rows and from each other:
  instance_id = "burnrate#<alarm name>" or "contfail#<alarm name>",
  event_time = "~STATE"

Events update the row only when newer (detail.state.timestamp), so duplicate
and out-of-order deliveries never overwrite a newer state. Every invocation
then reconciles: the notification owed for the CURRENT state is derived from
the row, not from the event that triggered the invocation.

Live and shadow keep separate ledgers: notified_* records what was actually
delivered (live only); shadow_notified_* records what shadow logged and is
never read by live.

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
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)

SOURCE = "aws.cloudwatch"
DETAIL_TYPE = "CloudWatch Alarm State Change"
STATES = {"ALARM", "OK", "INSUFFICIENT_DATA"}
STATE_KEY = "~STATE"

# Descriptive values for messages. They must match burn_rate.tf; a test
# compares them. The alarm itself is configured only in Terraform. The 14.4x
# design rationale for 9/60 is in the Availability Runbook §13.1, not here.
TARGET = "sunlightjetrans.com"
EVALUATION_PERIODS = 60
DATAPOINTS_TO_ALARM = 9
# Continuous failure alarm (continuous_failure.tf).
CONTINUOUS_EVALUATION_PERIODS = 6
CONTINUOUS_DATAPOINTS_TO_ALARM = 6
JST = timezone(timedelta(hours=9), "JST")

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
    return change


LIVE_LEDGER = "notified_state"
SHADOW_LEDGER = "shadow_notified_state"


def owed(item: dict, ledger: str = LIVE_LEDGER) -> str | None:
    """Which notification the current state still needs, given the last one recorded.

    ledger: "notified_state" (live: actually delivered) or
    "shadow_notified_state" (shadow: logged only). The two never mix.
    """
    current, delivered = item.get("alarm_state"), item.get(ledger)
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


KINDS = {
    "alarm": ("HIGH", "ALARM"),
    "normalized": ("HIGH", "NORMALIZED (alarm back to OK)"),
    "insufficient": ("WARN", "DATA INSUFFICIENT"),
    "data_restored": ("INFO", "DATA RESTORED"),
}
MEANING = {
    "alarm": "Many recent HTTP checks failed. Check the HTTP DOWN/RECOVERED and "
             "EC2/Spot notifications.",
    "normalized": "The CloudWatch alarm returned to OK: its 9-of-60 condition is no longer "
                  "met. This is NOT when the site recovered and does not confirm it is up "
                  "now; see Now and the HTTP DOWN/RECOVERED notifications.",
    "insufficient": "CloudWatch has too few HTTPUp datapoints to evaluate the alarm. This is "
                    "a monitoring data problem, not proof of downtime or recovery. Check the "
                    "scheduled HTTP probe (Lambda gmsv5-availability).",
    "data_restored": "The alarm has datapoints again and its condition is not met. This "
                     "does NOT confirm that the site is up now; see Now.",
}
FOOTNOTE = ("Now = latest HTTP check when this message was generated. Last 60 = the 60 "
            "completed minutes before the alarm transition, counted independently of "
            "CloudWatch's own evaluation (which can look further back).")
NO_CONTEXT = ["Now: UNKNOWN — HTTP state not read",
              "Last 60 completed min: unavailable (not read)"]

CONTINUOUS_MEANING = {
    # treat_missing_data = "missing" (continuous_failure.tf): OK needs a real
    # passed check; a stopped probe leads to INSUFFICIENT_DATA, never to OK.
    "alarm": "The 6 most recent HTTP checks all failed: an ongoing outage signal. A missing "
             "minute between failures does not break the run. Check the HTTP DOWN/RECOVERED "
             "and EC2/Spot notifications.",
    "normalized": "The CloudWatch alarm returned to OK: a passed check is among the 6 most "
                  "recent datapoints. This is NOT the confirmed recovery time and does not "
                  "confirm the site is up now; see Now and the HTTP DOWN/RECOVERED "
                  "notifications.",
    "insufficient": "CloudWatch has had no HTTPUp datapoints for about 12 minutes. This is a "
                    "monitoring data problem, not proof of downtime or recovery. Check the "
                    "scheduled HTTP probe (Lambda gmsv5-availability).",
    "data_restored": MEANING["data_restored"],
}
CONTINUOUS_FOOTNOTE = ("Now = latest HTTP check when this message was generated. Last 60 = "
                       "the 60 completed minutes before the alarm transition, counted "
                       "independently of CloudWatch's evaluation.")


@dataclass(frozen=True)
class Profile:
    """What differs between the monitored alarms. State handling is shared."""

    key: str
    title: str
    threshold: str
    row_prefix: str
    meaning: dict = field(repr=False)
    footnote: str = FOOTNOTE

    def header(self, kind: str) -> str:
        severity, label = KINDS[kind]
        return f"{severity} — {self.title}: {label}"


BURN_RATE = Profile(
    key="burn_rate", title="Availability Burn Rate",
    threshold=f"Alarm threshold: {DATAPOINTS_TO_ALARM}/{EVALUATION_PERIODS} failed datapoints",
    row_prefix="burnrate#", meaning=MEANING)
CONTINUOUS_FAILURE = Profile(
    key="continuous_failure", title="Availability Continuous Failure",
    threshold=(f"Alarm threshold: {CONTINUOUS_DATAPOINTS_TO_ALARM}/"
               f"{CONTINUOUS_EVALUATION_PERIODS} failed datapoints in a row "
               "(the most recent ones; missing minutes are skipped)"),
    row_prefix="contfail#", meaning=CONTINUOUS_MEANING, footnote=CONTINUOUS_FOOTNOTE)
HEADERS = {kind: BURN_RATE.header(kind) for kind in KINDS}


def context_lines(context, item: dict) -> list[str]:
    """Responder context. Never raises: a failure only drops the lines."""
    if context is None:
        return list(NO_CONTEXT)
    try:
        return list(context.lines(item["state_at"]))
    except Exception as exc:  # noqa: BLE001 - the notification must still go out.
        LOG.warning("Burn rate context failed: %s", type(exc).__name__)
        return list(NO_CONTEXT)


def transition_time(state_at: str) -> str:
    moment = datetime.fromisoformat(state_at.replace("Z", "+00:00"))
    return f"{moment.astimezone(JST):%H:%M:%S} JST ({state_at} UTC, alarm state timestamp)"


def message(kind: str, item: dict, alarm_name: str, context: list[str] | None = None,
            profile: Profile = BURN_RATE) -> str:
    lines = [
        profile.header(kind),
        profile.threshold,
        *(context if context is not None else NO_CONTEXT),
        f"Alarm: {item.get('previous_state', 'UNKNOWN')} -> {item['alarm_state']} "
        f"at {transition_time(item['state_at'])}",
        f"Target: {TARGET} (external HTTP probe, HTTPUp); alarm {alarm_name}",
        profile.meaning[kind],
        profile.footnote,
    ]
    return "\n".join(lines)


def is_conditional(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def row_key(alarm_name: str, profile: Profile = BURN_RATE) -> dict:
    return {"instance_id": profile.row_prefix + alarm_name, "event_time": STATE_KEY}


def accept(table, key: dict, change: dict, observed_at: str) -> str:
    """Store the change if it is newer than the stored state. Returns the outcome."""
    fields = dict(change, updated_at=observed_at)
    try:
        table.update_item(
            Key=key,
            UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
            ConditionExpression="attribute_not_exists(#state_at) OR #state_at < :state_at",
            ExpressionAttributeNames={f"#{k}": k for k in fields},
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


def shadow_mark(table, key: dict, at: str, state: str) -> bool:
    """Record a shadow (logged, not delivered) notification. At most once per state.

    Touches only shadow_notified_*; never notified_*, lease_* or the state.
    """
    try:
        table.update_item(
            Key=key,
            UpdateExpression="SET #shadow_state = :state, #shadow_at = :at",
            ConditionExpression=("#state_at = :at AND "
                                 "(attribute_not_exists(#shadow_at) OR #shadow_at <> :at)"),
            ExpressionAttributeNames={"#state_at": "state_at",
                                      "#shadow_state": "shadow_notified_state",
                                      "#shadow_at": "shadow_notified_at"},
            ExpressionAttributeValues={":state": state, ":at": at},
        )
        return True
    except ClientError as exc:
        if not is_conditional(exc):
            raise
    return False


def reconcile(table, key: dict, *, notify, now: int, deliver: bool, alarm_name: str,
              context=None, profile: Profile = BURN_RATE):
    """Send (live) or log (shadow) what the current state is owed.

    Live and shadow keep separate ledgers (notified_* and shadow_notified_*),
    so shadow follows the same lifecycle as live without ever counting as a
    delivery. Context (extra API calls) is read only for a message that is
    sent after a successful claim, or logged after a successful shadow mark.
    Duplicates, stale events, held leases and nothing-owed exit first.
    """
    sent = []
    for _ in range(MAX_RECONCILE_ROUNDS):
        item = current(table, key)
        if not item:
            break
        lease = item.get("lease_until")
        if lease is not None and int(lease) >= now:
            # Another invocation is sending. Its outcome decides what is owed.
            raise NotificationInFlight(f"lease held for {item.get('lease_at')}")
        kind = owed(item, LIVE_LEDGER if deliver else SHADOW_LEDGER)
        if kind is None:
            break
        at = item["state_at"]
        if not deliver:
            # Mark first: a redelivered or concurrent event then logs nothing.
            if not shadow_mark(table, key, at, item["alarm_state"]):
                continue  # State or shadow ledger changed. Re-read and decide again.
            text = message(kind, item, alarm_name, context_lines(context, item), profile)
            LOG.info(json.dumps({"burn_rate": "shadow", "delivered": False, "would_send": kind,
                                 "state_at": at, "alarm": profile.key, "message": text},
                                ensure_ascii=False))
            sent.append("shadow:" + kind)
            continue
        if not claim(table, key, at, now):
            continue  # State, delivery or lease changed. Re-read and decide again.
        try:
            notify(message(kind, item, alarm_name, context_lines(context, item), profile))
        except Exception:
            release(table, key, at)
            LOG.error(json.dumps({"burn_rate": "notify_failed", "kind": kind, "state_at": at}))
            raise
        mark(table, key, at, item["alarm_state"])
        LOG.info(json.dumps({"burn_rate": "notified", "kind": kind, "state_at": at,
                             "alarm": profile.key}))
        sent.append(kind)
    return sent


def handle(event: dict, *, table, notify, now, alarm_name: str, alarm_arn: str,
           delivery: str, context=None, profile: Profile = BURN_RATE):
    """context: burn_rate_context.Context (or None: messages say "not read").

    profile selects the message text and the state row; the name and ARN
    checks, ordering, lease and ledgers are the same for every alarm.
    """
    change = parse(event, alarm_name=alarm_name, alarm_arn=alarm_arn)
    if isinstance(change, str):
        return {"ignored": change}
    key = row_key(alarm_name, profile)
    moment = now()
    outcome = accept(table, key, change, moment.strftime("%Y-%m-%dT%H:%M:%SZ"))
    if outcome == "not_newer":
        stored = current(table, key).get("state_at", "")
        outcome = "duplicate" if stored == change["state_at"] else "stale"
    LOG.info(json.dumps({"burn_rate": outcome, "alarm_state": change["alarm_state"],
                         "state_at": change["state_at"], "alarm": profile.key}))
    # Duplicates and stale events still reconcile: a retry may complete a failed send.
    sent = reconcile(table, key, notify=notify, now=int(moment.timestamp()),
                     deliver=delivery == "enabled", alarm_name=alarm_name, context=context,
                     profile=profile)
    return {"burn_rate": outcome, "alarm_state": change["alarm_state"], "sent": sent}
