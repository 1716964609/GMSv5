"""Deployment monitoring, independent of HTTP availability."""
import re
import time
import uuid

from botocore.exceptions import ClientError

DOCUMENT = "GMSv5-DeployRelease"

EVENT_TYPE = (
    "EC2 Command Invocation Status-change Notification"
)

TERMINAL = {
    "Success", "Failed", "TimedOut",
    "Canceled", "Cancelled",
}


def classify(result):
    status = result["Status"]
    code = result.get("ResponseCode", -1)
    stdout = result.get("StandardOutputContent", "")

    sha = "unknown"

    match = re.search(
        r"(?m)^DEPLOYMENT_REQUESTED sha=([a-f0-9]{40})$",
        stdout,
    )

    if match:
        sha = match.group(1)

    outcome = "OUTCOME_UNKNOWN"

    if status == "Success" and code == 0:
        if sha != "unknown" and re.search(
            r"(?m)^(?:\S+ )?"
            r"(?:DEPLOYMENT_SUCCEEDED|ALREADY_DEPLOYED)"
            rf" release={sha}$",
            stdout,
        ):
            outcome = "SUCCEEDED"

    elif status == "Failed":
        outcome = {
            10: "FAILED_BEFORE_SWITCH",
            20: "ROLLED_BACK",
            21: "ROLLBACK_FAILED",
            22: "RECOVERY_REQUIRED",
        }.get(code, "OUTCOME_UNKNOWN")

    return outcome, sha, code


def lookup(client, command, instance):
    for attempt in range(5):
        try:
            result = client.get_command_invocation(
                CommandId=command,
                InstanceId=instance,
                PluginName="deployRelease",
            )

            if (
                result["CommandId"] != command
                or result["InstanceId"] != instance
            ):
                raise ValueError("Invocation identity mismatch")

            if result["Status"] in TERMINAL:
                return result

        except ClientError as error:
            if (
                error.response["Error"]["Code"]
                != "InvocationDoesNotExist"
            ):
                raise

        if attempt < 4:
            time.sleep(2 ** attempt)

    raise RuntimeError(
        "Terminal SSM result not available yet"
    )


def handle(event, *, app, ssm, save, notify, metric):
    if (
        event.get("source") != "aws.ssm"
        or event.get("detail-type") != EVENT_TYPE
    ):
        return {"ignored": "unsupported event"}

    detail = event.get("detail", {})

    if (
        detail.get("document-name") != DOCUMENT
        or detail.get("instance-id") != app
    ):
        return {"ignored": "unmonitored deployment"}

    command = str(detail.get("command-id", ""))

    try:
        if str(uuid.UUID(command)) != command:
            raise ValueError("Noncanonical command ID")
    except ValueError:
        return {"ignored": "invalid command ID"}

    status = detail.get("status")

    if status == "InProgress":
        outcome, sha, code = "IN_PROGRESS", "unknown", -1
        stage = "started"

    elif status in TERMINAL:
        result = lookup(ssm, command, app)

        outcome, sha, code = classify(result)
        stage = "finished"

    else:
        return {"ignored": "unsupported status"}

    # Separate deployment records from the EC2 availability
    # state and use a stable key across duplicate deliveries.
    created = save(
        "deployment#" + app,
        event,
        "deployment_" + outcome.lower(),
        "ssm",
        stable_key=f"command#{command}#{stage}",
    )

    if not created:
        return {"duplicate": True}

    metric(
        "DeploymentEvents",
        1,
        dimension=outcome,
        unit="Count",
    )

    notify(
        f"[GMS v5 DEPLOYMENT] {outcome}\n"
        f"Release: {sha}\n"
        f"Command: {command}\n"
        f"Exit code: {code}"
    )

    return {
        "outcome": outcome,
        "release": sha,
        "command_id": command,
        "response_code": code,
    }
