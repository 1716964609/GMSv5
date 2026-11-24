"""GMS v5 external availability pilot: EC2 events, HTTP checks, Discord alerts.

No third-party dependencies. AWS SDK boto3 is available in the Lambda Python 3.12 runtime.
Times are UTC. Metrics from periodic HTTP probes are samples, not exact uptime.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)

STATE_KEY = "~STATE"  # Deliberately reserved sort-key value; all actual event rows use UTC timestamps.
DATE_FMT = "%Y-%m-%dT%H:%M:%SZ"
STATE_EVENT = "EC2 Instance State-change Notification"
WARNING_EVENT = "EC2 Spot Instance Interruption Warning"
REBALANCE_EVENT = "EC2 Instance Rebalance Recommendation"
SPOT_SIGNAL_PHASES = {WARNING_EVENT: "warning", REBALANCE_EVENT: "rebalance"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def timestamp(value: str | datetime) -> str:
    return as_utc(value).strftime(DATE_FMT)


def seconds_between(start: str, end: str) -> int:
    return max(0, int((as_utc(end) - as_utc(start)).total_seconds()))


def configuration():
    return {
        "table": os.environ["TABLE_NAME"],
        "app": os.environ["APP_INSTANCE_ID"],
        "obs": os.environ["OBS_INSTANCE_ID"],
        "url": os.environ.get("HEALTH_URL", "https://sunlightjetrans.com/"),
        "relay": os.environ["DISCORD_CLOUDFRONT_URL"],
        "token_parameter": os.environ["DISCORD_TOKEN_PARAMETER"],
    }


@lru_cache(maxsize=1)
def table():
    return boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


@lru_cache(maxsize=1)
def cloudwatch():
    return boto3.client("cloudwatch")


@lru_cache(maxsize=1)
def ssm():
    return boto3.client("ssm")


@lru_cache(maxsize=1)
def relay_token():
    return ssm().get_parameter(
        Name=os.environ["DISCORD_TOKEN_PARAMETER"], WithDecryption=True
    )["Parameter"]["Value"].strip()


def notify(message: str):
    """Call the existing PUBLIC CloudFront directly; the private Nginx relay is unreachable."""
    url = os.environ["DISCORD_CLOUDFRONT_URL"]
    if not url.startswith("https://") or not url.endswith("/notify"):
        raise ValueError("Invalid HTTPS Discord CloudFront URL")
    body = json.dumps({"content": message[:1800], "allowed_mentions": {"parse": []}}).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-GMS-Notify-Token": relay_token(),
            "User-Agent": "GMSv5-Availability/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=8) as resp:
        if resp.status not in (200, 204):
            raise RuntimeError(f"Discord relay status {resp.status}")


def spot_enabled() -> bool:
    return os.environ.get("SPOT_STATUS_COLLECTION") == "enabled"


def spot_evidence(action: str, *args, **kwargs):
    """Spot reason evidence is optional. It must never break availability monitoring."""
    if not spot_enabled():
        return None
    try:
        import spot_status

        # The collector's own table client has bounded timeouts.
        return getattr(spot_status, action)(spot_status.table(), *args, **kwargs)
    except Exception as exc:  # Fail open by design.
        LOG.warning("Spot status evidence skipped (%s): %s", action, type(exc).__name__)
        return None


def spot_text(function: str, value, fallback: str) -> str:
    try:
        import spot_status

        return getattr(spot_status, function)(value)
    except Exception as exc:  # Fail open by design.
        LOG.warning("Spot status text unavailable: %s", type(exc).__name__)
        return fallback


def probe_http() -> bool:
    """HTTP 200 is an ingress-level check, not proof that translation APIs work."""
    request = urllib.request.Request(
        os.environ.get("HEALTH_URL", "https://sunlightjetrans.com/"),
        headers={"Cache-Control": "no-cache", "Pragma": "no-cache",
                 "User-Agent": "GMSv5-ExternalAvailability/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status == 200 and response.geturl().startswith("https://")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        LOG.warning("Public HTTP probe failed: %s", type(exc).__name__)
        return False


def metric(name: str, value: int, *, dimension: str, unit: str,
           instant: str | None = None):
    cloudwatch().put_metric_data(
        Namespace="GMS/Availability",
        MetricData=[{
            "MetricName": name,
            "Dimensions": [{"Name": "Target", "Value": dimension}],
            "Timestamp": as_utc(instant) if instant else now_utc(),
            "Value": value,
            "Unit": unit,
            "StorageResolution": 60,
        }],
    )


def read_state(instance_id: str):
    return table().get_item(Key={"instance_id": instance_id, "event_time": STATE_KEY},
                            ConsistentRead=True).get("Item", {})


def save_state(instance_id: str, values: dict):
    updates = [f"#{k} = :{k}" for k in values]
    names = {f"#{k}": k for k in values}
    vals = {f":{k}": v for k, v in values.items()}
    table().update_item(
        Key={"instance_id": instance_id, "event_time": STATE_KEY},
        UpdateExpression="SET " + ", ".join(updates),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=vals,
    )


def save_event(instance_id: str, event: dict, event_type: str,
               trigger: str = "unknown", stable_key=None) -> bool:
    """Idempotent, append-only event. Use timestamp plus event ID to avoid collisions."""
    key = stable_key if stable_key is not None else timestamp(event["time"]) + "#" + str(event.get("id", "no-id"))
    row = {
        "instance_id": instance_id,
        "event_time": key,
        "event_type": event_type,
        "source_event_id": str(event.get("id", "no-id")),
        "trigger": trigger,
        "expires_at": int(as_utc(event["time"]).timestamp()) + 400 * 86400,
    }
    if event_type == "stopped":
        row["stop_at"] = timestamp(event["time"])
    elif event_type == "running":
        row["running_at"] = timestamp(event["time"])
    try:
        table().put_item(Item=row,
                         ConditionExpression="attribute_not_exists(instance_id)")
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def find_latest_unclosed_stop(instance_id: str, running_at: str):
    """State events are sparse. Query bounded recent history to tolerate reordering."""
    result = table().query(
        KeyConditionExpression=Key("instance_id").eq(instance_id),
        ScanIndexForward=False,
        Limit=80,
        ConsistentRead=True,
    )
    for row in result.get("Items", []):
        if row.get("event_type") == "stopped" and row["stop_at"] <= running_at:
            if not row.get("running_at"):
                return row
    return None


def assign_running(instance_id: str, running_at: str):
    """Attach running timestamp to the latest open STOP incident, if present."""
    state = read_state(instance_id)
    key = state.get("active_stop")
    if key:
        stop = table().get_item(Key={"instance_id": instance_id,
                                     "event_time": key}, ConsistentRead=True).get("Item")
        if stop and stop.get("stop_at", "") <= running_at:
            target = stop
        else:
            target = find_latest_unclosed_stop(instance_id, running_at)
    else:
        target = find_latest_unclosed_stop(instance_id, running_at)
    if target:
        table().update_item(
            Key={"instance_id": instance_id, "event_time": target["event_time"]},
            UpdateExpression="SET running_at = if_not_exists(running_at, :running)",
            ExpressionAttributeValues={":running": running_at},
        )
        save_state(instance_id, {"active_stop": target["event_time"],
                                 "ec2_state": "running", "last_running": running_at})
        return target["event_time"]
    save_state(instance_id, {"ec2_state": "running", "last_running": running_at})
    return None


def complete_incident(instance_id: str, *, recovery_at: str, proof: str):
    """Close exactly once; app requires public HTTP 200, obs only EC2 running proof."""
    state = read_state(instance_id)
    key = state.get("active_stop")
    if not key:
        return False
    row = table().get_item(Key={"instance_id": instance_id,
                                "event_time": key}, ConsistentRead=True).get("Item")
    if not row or not row.get("running_at") or row.get("recovered_at"):
        return False
    end = max(recovery_at, row["running_at"])
    downtime = seconds_between(row["stop_at"], end)
    try:
        table().update_item(
            Key={"instance_id": instance_id, "event_time": key},
            UpdateExpression=("SET recovered_at = :end, downtime_seconds = :downtime, "
                              "recovery_proof = :proof"),
            ConditionExpression="attribute_not_exists(recovered_at)",
            ExpressionAttributeValues={":end": end, ":downtime": downtime,
                                       ":proof": proof},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise
    metric("StopToRecoverySeconds", downtime, dimension=instance_id, unit="Seconds",
           instant=end)
    suffix = "HTTP 200 を実測" if proof == "http200" else "EC2 running（サービス復旧は未確認）"
    message = f"[GMS v5 RECOVERED] {instance_id}: {downtime}s (event-time based); {suffix}"
    if spot_enabled():
        message += "\n" + spot_text("incident_summary", row,
                                     "Spot cause: UNKNOWN (summary unavailable)")
    notify(message)
    save_state(instance_id, {"active_stop": "", "last_recovered": end})
    return True


def poll_after_running(context):
    # Keep wall time below Lambda's 90s timeout. Scheduled minute probe is fallback.
    for _ in range(9):
        if probe_http():
            return timestamp(now_utc())
        if context is not None and context.get_remaining_time_in_millis() < 13000:
            break
        time.sleep(5)
    return None


def spot_trigger(state: dict, at: str) -> str:
    warning = state.get("last_spot_warning")
    if warning and 0 <= seconds_between(warning, at) <= 600 and warning <= at:
        return "spot_warning_observed"
    return "unknown"  # Cannot infer manual vs Spot from EC2 state-change alone.


def handle_ec2(event: dict, context=None):
    cfg = configuration()
    iid = event.get("detail", {}).get("instance-id")
    if iid not in {cfg["app"], cfg["obs"]}:
        return {"ignored": "unmonitored instance"}
    dt = event.get("detail-type")
    at = timestamp(event["time"])
    if dt == STATE_EVENT:
        state_name = event.get("detail", {}).get("state")
        if state_name == "stopping" and spot_enabled():
            # Evidence only: stopping never opens or closes an incident.
            spot_evidence("on_signal", iid, event, "stopping", STATE_KEY, context=context)
            return {"stored": "stopping"}
        if state_name not in ("stopped", "running"):
            return {"ignored": "other state"}
        prior_state = read_state(iid)
        trigger = spot_trigger(prior_state, at) if state_name == "stopped" else "unknown"
        created = save_event(iid, event, state_name, trigger)
        # Duplicate running events still retry pairing/recovery after earlier partial failures.
        if not created and state_name == "stopped":
            return {"duplicate": True}
        key = at + "#" + str(event.get("id", "no-id"))
        if state_name == "stopped":
            # When a late STOP event arrives after RUNNING, retain the newer EC2 state.
            later_running = prior_state.get("last_running", "")
            if later_running >= at:
                table().update_item(
                    Key={"instance_id": iid, "event_time": key},
                    UpdateExpression="SET running_at = if_not_exists(running_at, :running)",
                    ExpressionAttributeValues={":running": later_running},
                )
                save_state(iid, {"ec2_state": "running", "active_stop": key,
                                 "last_stopped": at})
                if iid == cfg["obs"]:
                    complete_incident(iid, recovery_at=later_running,
                                      proof="ec2_running_only")
                elif iid == cfg["app"]:
                    recovered = poll_after_running(context)
                    if recovered:
                        complete_incident(iid, recovery_at=recovered, proof="http200")
                spot_evidence("on_stopped", iid, event, key, prior_state,
                              context=context, retries=False)
                return {"stored": "late_stopped_paired"}
            save_state(iid, {"ec2_state": "stopped", "active_stop": key,
                             "last_stopped": at})
            LOG.info("EC2 stopped event recorded: %s", iid)
            # After the incident is registered: retries at about +2s and +5s.
            spot_evidence("on_stopped", iid, event, key, prior_state, context=context)
            return {"stored": "stopped"}
        key = assign_running(iid, at)
        if key and iid == cfg["obs"]:
            complete_incident(iid, recovery_at=at, proof="ec2_running_only")
        elif key and iid == cfg["app"]:
            recovered = poll_after_running(context)
            if recovered:
                complete_incident(iid, recovery_at=recovered, proof="http200")
        # After recovery processing, so the recovery timestamp is not delayed.
        spot_evidence("on_running", iid, event, key, context=context)
        return {"stored": "running", "incident": bool(key)}
    if dt in {WARNING_EVENT, REBALANCE_EVENT}:
        created = save_event(iid, event, dt, trigger="spot_signal")
        if created:
            # last_spot_warning keeps its historical meaning (warning OR rebalance).
            save_state(iid, {"last_spot_warning": at})
            kind = "INTERRUPTION WARNING" if dt == WARNING_EVENT else "REBALANCE RECOMMENDATION"
            try:
                notify(f"[GMS v5 {kind}] {iid} at {at}; {event.get('detail', {}).get('instance-action', 'unknown action')}")
            finally:
                # Evidence only after the notification, so it never delays it.
                spot_evidence("on_signal", iid, event, SPOT_SIGNAL_PHASES[dt],
                              STATE_KEY, context=context)
        return {"stored": dt, "new": created}
    return {"ignored": "other detail-type"}



HTTP_STATE_ID = "http#sunlightjetrans.com"


def track_http_status(event: dict, is_up: bool) -> dict:
    """Notify on two consecutive failures and the first recovery.

    The DynamoDB state is separate from EC2 incident records.
    Scheduled-minute ordering avoids counting duplicate deliveries.
    """
    slot = timestamp(event.get("time") or now_utc())[:16]
    observed = timestamp(now_utc())
    key = {"instance_id": HTTP_STATE_ID, "event_time": STATE_KEY}
    names = {
        "#slot": "last_slot",
        "#fail": "http_failures",
        "#status": "http_status",
        "#first": "first_fail_at",
        "#since": "down_since",
    }
    condition = "attribute_not_exists(#slot) OR #slot < :slot"

    try:
        if is_up:
            result = table().update_item(
                Key=key,
                UpdateExpression=(
                    "SET #slot = :slot, #fail = :zero, #status = :up "
                    "REMOVE #first, #since"
                ),
                ConditionExpression=condition,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues={
                    ":slot": slot, ":zero": 0, ":up": "up",
                },
                ReturnValues="ALL_OLD",
            )
            previous = result.get("Attributes", {})
            if previous.get("http_status") == "down":
                down_since = previous.get("down_since", "unknown")
                notify(
                    f"[GMS v5 HTTP RECOVERED] "
                    f"sunlightjetrans.com HTTP 200; "
                    f"observed={observed}; down_since={down_since}"
                )
                return {"transition": "recovered"}
            return {"transition": "healthy"}

        result = table().update_item(
            Key=key,
            UpdateExpression=(
                "SET #slot = :slot, "
                "#fail = if_not_exists(#fail, :zero) + :one, "
                "#first = if_not_exists(#first, :observed)"
            ),
            ConditionExpression=condition,
            ExpressionAttributeNames={
                "#slot": "last_slot",
                "#fail": "http_failures",
                "#first": "first_fail_at",
            },
            ExpressionAttributeValues={
                ":slot": slot,
                ":zero": 0,
                ":one": 1,
                ":observed": observed,
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return {"duplicate": True}
        raise

    item = result["Attributes"]
    failures = int(item.get("http_failures", 0))

    if failures < 2 or item.get("http_status") == "down":
        return {"transition": "pending_or_down", "failures": failures}

    first = item.get("first_fail_at", observed)
    try:
        table().update_item(
            Key=key,
            UpdateExpression="SET #status = :down, #since = :since",
            ConditionExpression=(
                "#slot = :slot AND "
                "(attribute_not_exists(#status) OR #status <> :down)"
            ),
            ExpressionAttributeNames={
                "#slot": "last_slot",
                "#status": "http_status",
                "#since": "down_since",
            },
            ExpressionAttributeValues={
                ":slot": slot, ":down": "down", ":since": first,
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return {"duplicate": True}
        raise

    notify(
        f"[GMS v5 HTTP DOWN] sunlightjetrans.com "
        f"failed {failures} consecutive checks; "
        f"first_failure={first}; detected={observed}"
    )
    return {"transition": "down", "failures": failures}


def handler(event: dict, context=None):
    cfg = configuration()
    # Synthetic smoke test never pretends a real EC2 was stopped.
    if event.get("source") == "gmsv5.manual-test" and event.get("test_mode") is True:
        at = timestamp(now_utc())
        mock = {"time": at, "id": event.get("id", "manual-test"), "detail": {}}
        created = save_event(
            "test-availability", mock, "synthetic_test", "manual_test",
            stable_key="synthetic#" + str(event.get("id", "manual-test")),
        )
        if created:
            metric("TestSignal", 1, dimension="synthetic", unit="Count")
            notify(f"[GMS v5 AVAILABILITY TEST] synthetic path OK at {at}; no EC2 stop")
        return {"synthetic": True, "new": created}
    # A one-minute EventBridge scheduled event probes independently of EC2 event delivery.
    if event.get("source") == "aws.events" and event.get("detail-type") == "Scheduled Event":
        available = probe_http()
        metric("HTTPUp", int(available), dimension="sunlightjetrans.com", unit="Count")
        track_http_status(event, available)
        if available and read_state(cfg["app"]).get("ec2_state") == "running":
            complete_incident(cfg["app"], recovery_at=timestamp(now_utc()), proof="http200")
        return {"http_up": available}
    if event.get("source") == "aws.ssm":
        from deployment import handle

        return handle(
            event,
            app=cfg["app"],
            ssm=ssm(),
            save=save_event,
            notify=notify,
            metric=metric,
        )
    if event.get("source") == "aws.ec2":
        return handle_ec2(event, context)
    return {"ignored": "unsupported source"}
