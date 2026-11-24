"""Spot interruption reason evidence for the availability Lambda.

Reads the AWS Spot request status around EC2 lifecycle events and stores it
next to the existing incident rows in the availability table.

Rules:
- Fail open. Callers wrap every entry point; this module also catches AWS
  errors itself so that one failed step does not skip the next.
- Never infer a cause. Only a Spot request status code confirms one. A Spot
  warning, a rebalance recommendation, the Spot price or the EC2 StateReason
  are stored as context only.
- Store every observation. Attach it to an incident only when its
  Status.UpdateTime falls inside that incident's time window.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)

ENABLE_VARIABLE = "SPOT_STATUS_COLLECTION"
OBSERVATION_PREFIX = "spot#"
OBSERVATION_TYPE = "spot_status_observation"
TTL_SECONDS = 400 * 86400

# Spot request status codes:
# https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-request-status.html
# https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-instances-request-status-lifecycle.html
REASONS = {
    "instance-stopped-no-capacity": "CAPACITY",
    "instance-stopped-by-price": "PRICE",
    "instance-stopped-by-user": "USER",
    "instance-stopped-by-experiment": "EXPERIMENT",
    "marked-for-stop": "PENDING",
    "marked-for-stop-by-experiment": "PENDING",
    "fulfilled": "UNKNOWN",
}
CONFIRMED = frozenset({"CAPACITY", "PRICE", "USER", "EXPERIMENT"})

PRE_STOP_PHASES = ("rebalance", "warning", "stopping")
SIGNAL_ATTRIBUTES = {
    "rebalance": "rebalance_recommendation_at",
    "warning": "interruption_warning_at",
    "stopping": "stopping_at",
}
STATE_ATTRIBUTES = {
    "rebalance": "last_rebalance_recommendation",
    "warning": "last_interruption_warning",
    "stopping": "last_stopping",
}

# A stop's status time is at most this far before or after the stop.
# Known restarts narrow the window further (see candidate_range).
PRE_STOP_WINDOW = timedelta(minutes=10)
POST_STOP_WINDOW = timedelta(minutes=10)
# Incidents closer than this can have overlapping windows.
NEIGHBOR_SPAN = PRE_STOP_WINDOW + POST_STOP_WINDOW
# Tolerance between the Lambda clock and AWS status times only.
CLOCK_GRACE = timedelta(seconds=60)
# Stopped-phase retries, in seconds after the first attempt.
RETRY_OFFSETS = (2, 5)

SDK_CONFIG = Config(
    connect_timeout=2,
    read_timeout=3,
    retries={"max_attempts": 2, "mode": "standard"},
)
# Worst case of one SDK call with SDK_CONFIG: two attempts of
# (2 s connect + 3 s read) and one standard-mode backoff of at most
# 1 s, raised by at most 5 s by an x-amz-retry-after header.
CALL_WORST_MS = 2 * (2000 + 3000) + (1000 + 5000)
# Left for the handler to return after the collector stops.
SAFETY_MS = 2000
# An attempt starts only if it can read and persist one observation:
# DescribeInstances, DescribeSpotInstanceRequests and one PutItem.
ATTEMPT_WORST_MS = 3 * CALL_WORST_MS

RETRYABLE = frozenset({"unresolved", "superseded", "unavailable", "stale",
                       "no_update_time"})


def enabled() -> bool:
    return os.environ.get(ENABLE_VARIABLE) == "enabled"


@lru_cache(maxsize=1)
def ec2():
    return boto3.client("ec2", config=SDK_CONFIG)


@lru_cache(maxsize=1)
def table():
    """Own DynamoDB client with bounded timeouts; the handler's is unbounded."""
    return boto3.resource("dynamodb", config=SDK_CONFIG).Table(os.environ["TABLE_NAME"])


class BudgetExhausted(Exception):
    """Not enough Lambda time left for another worst-case AWS call."""


class Budget:
    def __init__(self, context):
        self.context = context

    def allows(self, needed_ms: float) -> bool:
        if self.context is None:
            return True
        return self.context.get_remaining_time_in_millis() - needed_ms >= SAFETY_MS

    def require(self, needed_ms: float) -> None:
        if not self.allows(needed_ms):
            raise BudgetExhausted()


class Guarded:
    """Checks the budget before every AWS call made through the wrapped object."""

    def __init__(self, target, budget: Budget):
        self._target = target
        self._budget = budget

    def __getattr__(self, name):
        attribute = getattr(self._target, name)
        if not callable(attribute):
            return attribute

        def call(*args, **kwargs):
            self._budget.require(CALL_WORST_MS)
            return attribute(*args, **kwargs)

        return call


def guard(target, budget: Budget):
    return target if isinstance(target, Guarded) else Guarded(target, budget)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def iso(value) -> str | None:
    moment = _utc(value)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def iso_ms(value) -> str:
    moment = _utc(value)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def classify(code) -> str:
    return REASONS.get(code or "", "UNKNOWN")


def _conditional_failed(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


# --- AWS reads -------------------------------------------------------------

def observe(instance_id: str, *, client, now=None) -> dict:
    """One read-only observation. Errors are recorded, never raised."""
    now = now or now_utc
    obs = {"classification": "UNKNOWN"}
    try:
        reservations = client.describe_instances(InstanceIds=[instance_id])
        instances = [
            item
            for reservation in reservations.get("Reservations", [])
            for item in reservation.get("Instances", [])
            if item.get("InstanceId") == instance_id
        ]
        if not instances:
            obs["error"] = "instance_not_found"
            return obs
        instance = instances[0]
        reason = instance.get("StateReason") or {}
        obs.update({
            "ec2_state": (instance.get("State") or {}).get("Name"),
            "launch_time": iso(instance.get("LaunchTime")),
            "instance_type": instance.get("InstanceType"),
            "availability_zone": (instance.get("Placement") or {}).get("AvailabilityZone"),
            "instance_lifecycle": instance.get("InstanceLifecycle"),
            "state_reason_code": reason.get("Code"),
            "state_reason_message": reason.get("Message"),
        })
        request_id = instance.get("SpotInstanceRequestId")
        if not request_id:
            obs["error"] = "missing_spot_request_id"
            return obs
        obs["spot_request_id"] = request_id

        response = client.describe_spot_instance_requests(
            SpotInstanceRequestIds=[request_id]
        )
        matches = [
            item for item in response.get("SpotInstanceRequests", [])
            if item.get("SpotInstanceRequestId") == request_id
        ]
        if not matches:
            obs["error"] = "spot_request_not_found"
            return obs
        request = matches[0]
        if request.get("InstanceId") not in (None, instance_id):
            obs["error"] = "spot_request_instance_mismatch"
            return obs
        status = request.get("Status") or {}
        obs.update({
            "status_code": status.get("Code"),
            "status_message": status.get("Message"),
            "status_update_time": iso(status.get("UpdateTime")),
            "request_state": request.get("State"),
            "max_price": request.get("SpotPrice"),
            "interruption_behavior": request.get("InstanceInterruptionBehavior"),
        })
        obs["classification"] = classify(obs["status_code"])
    except ClientError as exc:
        obs["error"] = exc.response.get("Error", {}).get("Code", "ClientError")
    except (BotoCoreError, KeyError, TypeError, ValueError, AttributeError) as exc:
        obs["error"] = type(exc).__name__
    finally:
        obs["observed_at"] = iso_ms(now())
    return obs


# --- Incident window -----------------------------------------------------------

def get_incident(db, instance_id: str, key):
    if not key:
        return None
    item = db.get_item(Key={"instance_id": instance_id, "event_time": key},
                       ConsistentRead=True).get("Item")
    if item and item.get("event_type") == "stopped":
        return item
    return None


def get_neighbors(db, instance_id: str, incident: dict) -> list:
    """Other incidents whose evidence windows could overlap this one."""
    stop = _utc(incident["stop_at"])
    result = db.query(
        KeyConditionExpression=(
            Key("instance_id").eq(instance_id)
            & Key("event_time").between(iso(stop - NEIGHBOR_SPAN),
                                        iso(stop + NEIGHBOR_SPAN) + "~")
        ),
        ConsistentRead=True,
        Limit=100,
    )
    return [item for item in result.get("Items", [])
            if item.get("event_type") == "stopped" and item.get("stop_at")
            and item.get("event_time") != incident.get("event_time")]


def restarts_before(incident: dict) -> list:
    """Known launches of the run that this stop ended. Its status is not older."""
    stop = _utc(incident["stop_at"])
    found = [_utc(incident.get(name))
             for name in ("spot_previous_running_at", "spot_run_started_at")]
    return [moment for moment in found if moment and moment <= stop]


def candidate_range(incident: dict, launched=None):
    """[lower, upper) of Status.UpdateTime values that can belong to this stop.

    A stop's status is set during the run that the stop ended, so it is never
    older than that run's launch and never newer than the next launch. No
    tolerance is added at the restart: a status set after the instance was
    started again belongs to a later cycle.
    """
    stop = _utc(incident["stop_at"])
    lower = stop - PRE_STOP_WINDOW
    start = _utc(incident.get("spot_window_start"))
    if start:
        lower = max(lower, start)
    for moment in restarts_before(incident):
        lower = max(lower, moment)
    upper = stop + POST_STOP_WINDOW
    running = _utc(incident.get("running_at"))
    if running and running >= stop:
        upper = min(upper, running)
    if launched:
        if launched <= stop:
            lower = max(lower, launched)
        else:
            upper = min(upper, launched)
    return lower, upper


def attribute(obs: dict, incident, neighbors=()) -> str:
    """Return "in_window" only when the status belongs to this incident alone."""
    if obs.get("error"):
        return "unavailable"
    if incident is None:
        return "no_incident"
    if not incident.get("spot_window_start"):
        return "legacy_incident"  # Created before this feature: no backfill.
    updated = _utc(obs.get("status_update_time"))
    if updated is None:
        return "no_update_time"
    known_request = incident.get("spot_request_id")
    if known_request and obs.get("spot_request_id") != known_request:
        return "request_mismatch"

    stop = _utc(incident["stop_at"])
    launched = _utc(obs.get("launch_time"))
    lower, upper = candidate_range(incident, launched)
    upper = min(upper, _utc(obs["observed_at"]) + CLOCK_GRACE)
    ours = restarts_before(incident)
    if launched and launched <= stop:
        ours.append(launched)
    later = []
    for other in neighbors:
        if _utc(other["stop_at"]) > stop:
            # A later incident's known run start is a restart after this stop.
            later.extend(moment for moment in restarts_before(other) if moment > stop)
    for moment in later:
        upper = min(upper, moment)
    if updated < lower:
        return "stale"
    if updated >= upper:
        return "after_incident"

    for other in neighbors:
        other_stop = _utc(other["stop_at"])
        other_lower, other_upper = candidate_range(other)
        if other_stop < stop:
            # This run started after the earlier stop, so it ended before then.
            for moment in ours:
                if moment > other_stop:
                    other_upper = min(other_upper, moment)
        if other_lower <= updated < other_upper:
            return "ambiguous"  # Keep the observation; confirm nothing.
    return "in_window"


def signal_belongs(at, incident: dict, neighbors=()) -> bool:
    """A warning, rebalance or stopping signal belongs to the first stop after it."""
    moment = _utc(at)
    stop = _utc(incident["stop_at"])
    if moment > stop or moment < stop - PRE_STOP_WINDOW:
        return False
    start = _utc(incident.get("spot_window_start"))
    if start and moment < start:
        return False
    previous_stop = _utc(incident.get("spot_previous_stop_at"))
    if previous_stop and moment <= previous_stop:
        return False
    if any(moment < launch for launch in restarts_before(incident)):
        return False
    return not any(moment <= _utc(other["stop_at"]) < stop for other in neighbors)


# --- DynamoDB writes ----------------------------------------------------------

def observation_key(event: dict, phase: str, attempt: int) -> str:
    return f"{iso(event['time'])}#{event.get('id', 'no-id')}#{phase}#{attempt}"


def save_observation(db, instance_id, obs, *, phase, event, attempt,
                     attribution, incident_key=None):
    """Append-only; a duplicate delivery of the same event writes nothing."""
    key = observation_key(event, phase, attempt)
    item = {name: value for name, value in obs.items() if value not in (None, "")}
    item.update({
        "instance_id": OBSERVATION_PREFIX + instance_id,
        "event_time": key,
        "event_type": OBSERVATION_TYPE,
        "monitored_instance_id": instance_id,
        "phase": phase,
        "source_event_id": str(event.get("id", "no-id")),
        "source_event_time": iso(event["time"]),
        "attempt": attempt,
        "authoritative": obs.get("classification") in CONFIRMED,
        "attribution": attribution,
        "expires_at": int(_utc(event["time"]).timestamp()) + TTL_SECONDS,
    })
    if incident_key:
        item["incident_key"] = incident_key
    try:
        db.put_item(Item=item, ConditionExpression="attribute_not_exists(instance_id)")
        return key
    except ClientError as exc:
        if _conditional_failed(exc):
            return None
        raise


def mark_observation(db, instance_id, key, *, attribution=None, result=None,
                     incident_key=None):
    values = {"attribution": attribution, "incident_result": result,
              "incident_key": incident_key}
    values = {name: value for name, value in values.items() if value}
    names = {f"#{name}": name for name in values}
    names["#et"] = "event_type"
    try:
        db.update_item(
            Key={"instance_id": OBSERVATION_PREFIX + instance_id, "event_time": key},
            UpdateExpression="SET " + ", ".join(f"#{n} = :{n}" for n in values),
            ConditionExpression="attribute_exists(#et)",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues={f":{n}": v for n, v in values.items()},
        )
    except ClientError as exc:
        if not _conditional_failed(exc):
            raise


def apply_to_incident(db, instance_id, key, obs, phase) -> str:
    """Attach in-window evidence. Confirmed evidence is never overwritten."""
    confirmed = obs["classification"] in CONFIRMED
    values = {
        "spot_reason": obs["classification"],
        "spot_status_code": obs.get("status_code") or "unavailable",
        "spot_status_message": obs.get("status_message") or "",
        "spot_status_update_time": obs["status_update_time"],
        "spot_evidence_observed_at": obs["observed_at"],
        "spot_evidence_phase": phase,
        "spot_request_id": obs.get("spot_request_id") or "unavailable",
        "spot_reason_confirmed": confirmed,
    }
    names = {f"#{name}": name for name in values}
    names["#et"] = "event_type"
    attribute_values = {f":{name}": value for name, value in values.items()}
    attribute_values[":stopped"] = "stopped"
    attribute_values[":false"] = False
    condition = ("#et = :stopped AND (attribute_not_exists(#spot_reason_confirmed) "
                 "OR #spot_reason_confirmed = :false)")
    if not confirmed:
        # An older unresolved status never replaces a newer one.
        condition += (" AND (attribute_not_exists(#spot_status_update_time) "
                      "OR #spot_status_update_time <= :spot_status_update_time)")
    try:
        db.update_item(
            Key={"instance_id": instance_id, "event_time": key},
            UpdateExpression="SET " + ", ".join(f"#{n} = :{n}" for n in values),
            ConditionExpression=condition,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=attribute_values,
        )
        return "confirmed" if confirmed else "unresolved"
    except ClientError as exc:
        if not _conditional_failed(exc):
            raise
    if not confirmed:
        return "superseded"
    return _record_conflict(db, instance_id, key, obs)


def _record_conflict(db, instance_id, key, obs) -> str:
    row = db.get_item(Key={"instance_id": instance_id, "event_time": key},
                      ConsistentRead=True).get("Item") or {}
    if row.get("event_type") != "stopped" or not row.get("spot_reason_confirmed"):
        return "no_incident"
    code = obs["status_code"]
    if row.get("spot_status_code") == code:
        return "duplicate"
    try:
        db.update_item(
            Key={"instance_id": instance_id, "event_time": key},
            UpdateExpression="SET #flag = :true ADD #codes :codes",
            ConditionExpression="#et = :stopped AND #conf = :true AND #code <> :code",
            ExpressionAttributeNames={
                "#flag": "spot_reason_conflict",
                "#codes": "spot_conflict_codes",
                "#et": "event_type",
                "#conf": "spot_reason_confirmed",
                "#code": "spot_status_code",
            },
            ExpressionAttributeValues={
                ":true": True, ":codes": {code}, ":stopped": "stopped", ":code": code,
            },
        )
    except ClientError as exc:
        if not _conditional_failed(exc):
            raise
        return "duplicate"
    LOG.warning("Conflicting Spot status for %s incident %s: kept %s, also saw %s",
                instance_id, key, row.get("spot_status_code"), code)
    return "conflict"


def init_incident(db, instance_id, key, *, stop_at, prior_state) -> None:
    """Give a new stopped row its evidence window and signal timestamps."""
    stop = _utc(stop_at)
    lower = stop - PRE_STOP_WINDOW
    values = {}
    last_running = _utc(prior_state.get("last_running"))
    if last_running and last_running < stop:
        lower = max(lower, last_running)
        values["spot_previous_running_at"] = iso(last_running)
    previous_stop = _utc(prior_state.get("last_stopped"))
    if previous_stop and previous_stop < stop:
        values["spot_previous_stop_at"] = iso(previous_stop)
    values.update({
        "spot_window_start": iso(lower),
        "spot_reason": "UNKNOWN",
        "spot_reason_confirmed": False,
    })
    for phase, state_name in STATE_ATTRIBUTES.items():
        at = _utc(prior_state.get(state_name))
        # Signals precede their own stop and follow the previous one.
        if at and lower <= at <= stop and not (previous_stop and at <= previous_stop
                                               and previous_stop < stop):
            values[SIGNAL_ATTRIBUTES[phase]] = iso(at)
    names = {f"#{name}": name for name in values}
    names["#et"] = "event_type"
    attribute_values = {f":{name}": value for name, value in values.items()}
    attribute_values[":stopped"] = "stopped"
    try:
        db.update_item(
            Key={"instance_id": instance_id, "event_time": key},
            UpdateExpression="SET " + ", ".join(
                f"#{n} = if_not_exists(#{n}, :{n})" for n in values),
            ConditionExpression="#et = :stopped",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=attribute_values,
        )
    except ClientError as exc:
        if not _conditional_failed(exc):
            raise


def _set_if_missing(db, instance_id, key, name, value) -> None:
    try:
        db.update_item(
            Key={"instance_id": instance_id, "event_time": key},
            UpdateExpression="SET #a = if_not_exists(#a, :v)",
            ConditionExpression="#et = :stopped",
            ExpressionAttributeNames={"#a": name, "#et": "event_type"},
            ExpressionAttributeValues={":v": value, ":stopped": "stopped"},
        )
    except ClientError as exc:
        if not _conditional_failed(exc):
            raise


def set_signal(db, instance_id, key, phase, at) -> None:
    _set_if_missing(db, instance_id, key, SIGNAL_ATTRIBUTES[phase], at)


def set_run_started(db, instance_id, incident, launch_time) -> None:
    """The launch seen while the instance had not restarted since this stop."""
    launched = _utc(launch_time)
    if launched and launched <= _utc(incident["stop_at"]) \
            and not incident.get("spot_run_started_at"):
        _set_if_missing(db, instance_id, incident["event_time"],
                        "spot_run_started_at", iso(launched))
        incident["spot_run_started_at"] = iso(launched)


def record_signal_state(db, instance_id, phase, at, state_key) -> None:
    """Keep the newest signal time per kind; older deliveries never win."""
    try:
        db.update_item(
            Key={"instance_id": instance_id, "event_time": state_key},
            UpdateExpression="SET #a = :at",
            ConditionExpression="attribute_not_exists(#a) OR #a < :at",
            ExpressionAttributeNames={"#a": STATE_ATTRIBUTES[phase]},
            ExpressionAttributeValues={":at": at},
        )
    except ClientError as exc:
        if not _conditional_failed(exc):
            raise


def _stored_observation(item: dict) -> dict:
    fields = ("classification", "status_code", "status_message", "status_update_time",
              "observed_at", "spot_request_id", "launch_time", "error")
    return {name: item.get(name) for name in fields}


def attach(db, instance_id, incident_key, obs, phase, obs_key, incident=None) -> str:
    """Decide whether a saved observation belongs to the incident, then record why."""
    incident = incident or get_incident(db, instance_id, incident_key)
    neighbors = ()
    if incident is not None and incident.get("spot_window_start"):
        if obs.get("launch_time"):
            set_run_started(db, instance_id, incident, obs["launch_time"])
        neighbors = get_neighbors(db, instance_id, incident)
    attribution = attribute(obs, incident, neighbors)
    result = attribution
    if attribution == "in_window":
        result = apply_to_incident(db, instance_id, incident_key, obs, phase)
    mark_observation(db, instance_id, obs_key, attribution=attribution, result=result,
                     incident_key=incident_key)
    return result


def associate_pre_incident(db, instance_id, key) -> int:
    """Attach observations made before the stopped event arrived."""
    incident = get_incident(db, instance_id, key)
    if not incident or not incident.get("spot_window_start"):
        return 0
    result = db.query(
        KeyConditionExpression=(
            Key("instance_id").eq(OBSERVATION_PREFIX + instance_id)
            & Key("event_time").between(incident["spot_window_start"],
                                        iso(incident["stop_at"]) + "~")
        ),
        ConsistentRead=True,
        Limit=50,
    )
    neighbors = get_neighbors(db, instance_id, incident)
    attached = 0
    for item in result.get("Items", []):
        phase = item.get("phase")
        if item.get("event_type") != OBSERVATION_TYPE or phase not in PRE_STOP_PHASES:
            continue
        if signal_belongs(item["source_event_time"], incident, neighbors):
            set_signal(db, instance_id, key, phase, item["source_event_time"])
        outcome = attach(db, instance_id, key, _stored_observation(item), phase,
                         item["event_time"], incident=incident)
        attached += outcome in ("confirmed", "unresolved", "duplicate", "conflict")
    return attached


# --- Collection ------------------------------------------------------------------

def record(db, instance_id, obs, *, phase, event, attempt, incident_key) -> dict:
    """Persist first, then attach. Useful evidence is never held back."""
    if incident_key:
        attribution = "pending"
    elif obs.get("error"):
        attribution = "unavailable"
    else:
        attribution = "pre_incident" if phase in PRE_STOP_PHASES else "no_incident"
    key = save_observation(db, instance_id, obs, phase=phase, event=event,
                           attempt=attempt, attribution=attribution,
                           incident_key=incident_key)
    if key is None:
        return {"result": "duplicate_observation", "observation": obs, "key": None}
    result = attribution
    if incident_key:
        result = attach(db, instance_id, incident_key, obs, phase, key)
    LOG.info("Spot status %s %s attempt=%s code=%s class=%s result=%s",
             instance_id, phase, attempt, obs.get("status_code"),
             obs.get("classification"), result)
    return {"result": result, "observation": obs, "key": key}


def collect(db, instance_id, *, phase, event, incident_key=None, client=None,
            context=None, retries=False, sleep=None, clock=None, now=None,
            schedule=None, started=None) -> list:
    """Read and persist observations. schedule: (attempt, seconds after start)."""
    budget = Budget(context)
    db = guard(db, budget)
    client = guard(client or ec2(), budget)
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    if schedule is None:
        offsets = (0,) + RETRY_OFFSETS if retries else (0,)
        schedule = list(enumerate(offsets))
    started = clock() if started is None else started
    results = []
    for attempt, offset in schedule:
        wait = max(0.0, offset - (clock() - started))
        # Checked before every attempt, including the waiting time.
        if not budget.allows(wait * 1000 + ATTEMPT_WORST_MS):
            results.append({"result": "skipped_time_budget", "observation": None,
                            "key": None})
            break
        if wait:
            sleep(wait)
        try:
            obs = observe(instance_id, client=client, now=now)
            outcome = record(db, instance_id, obs, phase=phase, event=event,
                             attempt=attempt, incident_key=incident_key)
        except BudgetExhausted:
            results.append({"result": "skipped_time_budget", "observation": None,
                            "key": None})
            break
        results.append(outcome)
        if outcome["result"] not in RETRYABLE:
            break
    return results


STEP_ERRORS = (ClientError, BotoCoreError, BudgetExhausted)


def on_stopped(db, instance_id, event, incident_key, prior_state, *, context=None,
               retries=True, client=None, sleep=None, clock=None):
    """Order: window, first observation, earlier observations, then retries.

    The first read comes before linking older observations so that a short
    budget is spent on the status that can still change.
    """
    db = guard(db, Budget(context))
    clock = clock or time.monotonic
    started = clock()
    common = dict(phase="stopped", event=event, incident_key=incident_key,
                  client=client, context=context, sleep=sleep, clock=clock,
                  started=started)
    try:
        init_incident(db, instance_id, incident_key, stop_at=event["time"],
                      prior_state=prior_state)
    except STEP_ERRORS as exc:
        LOG.warning("Spot incident window not stored: %s", type(exc).__name__)
    results = collect(db, instance_id, schedule=[(0, 0)], **common)
    try:
        associate_pre_incident(db, instance_id, incident_key)
    except STEP_ERRORS as exc:
        LOG.warning("Earlier Spot observations not linked: %s", type(exc).__name__)
    if retries and results and results[-1]["result"] in RETRYABLE:
        results += collect(db, instance_id,
                           schedule=[(n + 1, offset) for n, offset in enumerate(RETRY_OFFSETS)],
                           **common)
    return results


def on_running(db, instance_id, event, incident_key, *, context=None, client=None):
    return collect(db, instance_id, phase="running", event=event,
                   incident_key=incident_key, client=client, context=context)


def on_signal(db, instance_id, event, phase, state_key, *, context=None, client=None):
    """Warning, rebalance or stopping. Save first, then look for an incident."""
    db = guard(db, Budget(context))
    at = iso(event["time"])
    try:
        record_signal_state(db, instance_id, phase, at, state_key)
    except STEP_ERRORS as exc:
        LOG.warning("Spot signal state not stored: %s", type(exc).__name__)
    results = collect(db, instance_id, phase=phase, event=event, client=client,
                      context=context)
    try:
        # Usually no incident exists yet. It does when this event arrives late.
        state = db.get_item(Key={"instance_id": instance_id, "event_time": state_key},
                            ConsistentRead=True).get("Item", {})
        incident_key = state.get("active_stop")
        incident = get_incident(db, instance_id, incident_key)
        if incident and incident.get("spot_window_start"):
            neighbors = get_neighbors(db, instance_id, incident)
            if signal_belongs(at, incident, neighbors):
                set_signal(db, instance_id, incident_key, phase, at)
            for outcome in results:
                obs = outcome.get("observation")
                if outcome.get("key") and obs:
                    outcome["result"] = attach(db, instance_id, incident_key, obs, phase,
                                               outcome["key"], incident=incident)
    except STEP_ERRORS as exc:
        LOG.warning("Late Spot signal not linked: %s", type(exc).__name__)
    return results


# --- Discord text ------------------------------------------------------------------

def incident_summary(row: dict) -> str:
    code = row.get("spot_status_code")
    observed = row.get("spot_evidence_observed_at")
    if row.get("spot_reason_confirmed") is True and code:
        text = (f"Spot cause: {row.get('spot_reason')} (confirmed by AWS Spot request status); "
                f"AWS code: {code}; status updated: {row.get('spot_status_update_time')}; "
                f"observed: {observed}")
        conflicts = row.get("spot_conflict_codes")
        if conflicts:
            text += f"; CONFLICT also observed: {', '.join(sorted(conflicts))}"
        return text
    if code:
        return (f"Spot cause: {row.get('spot_reason') or 'UNKNOWN'} (not confirmed); "
                f"last AWS code: {code}; observed: {observed}")
    return "Spot cause: UNKNOWN (no AWS Spot status evidence for this incident)"
