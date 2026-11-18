#!/usr/bin/env python3

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid

from pathlib import Path
from state import validate_sha, timestamp

DOCUMENT = "GMSv5-DeployRelease"
PLUGIN = "deployRelease"
REGION = "ap-northeast-1"

POLL_INTERVAL = 10
POLL_TIMEOUT = 2100
DELIVERY_TIMEOUT = 120

ACTIVE_STATUSES = {
    "Pending",
    "InProgress",
    "Delayed",
    "Cancelling",
}


def api(operation, *args):
    # Do not retry SendCommand automatically.
    # Its response may be lost after AWS accepts it.
    attempts = "1" if operation == "send-command" else "3"

    result = subprocess.run(
        [
            "aws", "ssm", operation,
            "--region", REGION,
            "--output", "json",
            "--cli-connect-timeout", "5",
            "--cli-read-timeout", "10",
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=45,
        env={
            **os.environ,
            "AWS_PAGER": "",
            "AWS_RETRY_MODE": "standard",
            "AWS_MAX_ATTEMPTS": attempts,
        },
    )

    return json.loads(result.stdout)


def validate_target(instance, version, sha):
    validate_sha(sha)

    if not re.fullmatch(
        r"i-[0-9a-f]{8,17}", instance
    ):
        raise ValueError("Invalid instance ID")

    if not re.fullmatch(r"[1-9][0-9]*", version):
        raise ValueError("Explicit document version required")


def save_report(path, report):
    path = Path(path)

    report["updated_at"] = timestamp()

    fd, name = tempfile.mkstemp(
        prefix=".ssm-report-",
        dir=path.parent,
    )

    temporary = Path(name)

    try:
        with os.fdopen(fd, "w") as file:
            json.dump(report, file, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        os.replace(temporary, path)

    finally:
        temporary.unlink(missing_ok=True)


def classify(invocation, sha):
    status = invocation["Status"]
    code = invocation.get("ResponseCode", -1)

    if status in ACTIVE_STATUSES:
        return "IN_PROGRESS", None

    if status == "Success" and code == 0:
        stdout = invocation.get(
            "StandardOutputContent", ""
        )

        markers = (
            f"DEPLOYMENT_SUCCEEDED release={sha}",
            f"ALREADY_DEPLOYED release={sha}",
        )

        if any(
            line.endswith(marker)
            for line in stdout.splitlines()
            for marker in markers
        ):
            return "SUCCEEDED", 0

        return "OUTCOME_UNKNOWN", 22

    if status == "Failed":
        outcomes = {
            10: ("FAILED_BEFORE_SWITCH", 10),
            20: ("ROLLED_BACK", 20),
            21: ("ROLLBACK_FAILED", 21),
            22: ("RECOVERY_REQUIRED", 22),
        }

        return outcomes.get(
            code, ("OUTCOME_UNKNOWN", 22)
        )

    # Timeout, cancellation and unexpected statuses
    # cannot prove application recovery.
    return "OUTCOME_UNKNOWN", 22


def poll(report, path, timeout=POLL_TIMEOUT):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            invocation = api(
                "get-command-invocation",
                "--command-id", report["command_id"],
                "--instance-id", report["instance_id"],
                "--plugin-name", PLUGIN,
            )

            if (
                invocation["CommandId"]
                != report["command_id"]
                or invocation["InstanceId"]
                != report["instance_id"]
            ):
                raise ValueError("Unexpected invocation identity")

        except (
            subprocess.SubprocessError,
            OSError,
            ValueError,
            KeyError,
        ) as error:

            print(
                f"SSM_POLL_UNAVAILABLE type={type(error).__name__}",
                flush=True,
            )

        else:
            outcome, exit_code = classify(
                invocation,
                report["release_sha"],
            )

            report.update({
                "ssm_status": invocation["Status"],
                "ssm_status_details": invocation.get(
                    "StatusDetails", ""
                ),
                "response_code": invocation.get(
                    "ResponseCode", -1
                ),
                "outcome": outcome,
            })

            save_report(path, report)

            print(
                f"SSM_STATUS={invocation['Status']} "
                f"OUTCOME={outcome}",
                flush=True,
            )

            if exit_code is not None:
                return exit_code

        remaining = deadline - time.monotonic()

        if remaining > 0:
            time.sleep(min(POLL_INTERVAL, remaining))

    report["outcome"] = "OUTCOME_UNKNOWN"
    report["reason"] = "polling_deadline_exceeded"

    save_report(path, report)

    return 22


def execute(instance, version, sha, path, request_id=None):
    validate_target(instance, version, sha)

    path = Path(path)

    if path.exists() or path.is_symlink():
        raise ValueError(
            "Report already exists; do not resubmit"
        )

    request_id = request_id or uuid.uuid4().hex

    if not re.fullmatch(r"[a-f0-9]{32}", request_id):
        raise ValueError("Invalid request ID")

    report = {
        "version": 1,
        "request_id": request_id,
        "release_sha": sha,
        "instance_id": instance,
        "document_name": DOCUMENT,
        "document_version": version,
        "command_id": None,
        "outcome": "SUBMISSION_PENDING",
        "started_at": timestamp(),
    }

    # Reserve the report before issuing the request.
    with path.open("x"):
        pass

    save_report(path, report)

    try:
        result = api(
            "send-command",
            "--instance-ids", instance,
            "--document-name", DOCUMENT,
            "--document-version", version,
            "--timeout-seconds", str(DELIVERY_TIMEOUT),
            "--parameters",
            json.dumps({"ReleaseSha": [sha]}),
            "--comment", f"gmsv5-deploy-{request_id}",
        )

        command_id = result["Command"]["CommandId"]

        uuid.UUID(command_id)

    except (
        subprocess.SubprocessError,
        OSError,
        KeyError,
        ValueError,
        TypeError,
    ):
        report["outcome"] = "SUBMISSION_UNKNOWN"
        report["reason"] = "send_command_not_confirmed"

        save_report(path, report)

        print("SSM_SUBMISSION_UNKNOWN", flush=True)
        return 22

    report["command_id"] = command_id
    report["outcome"] = "IN_PROGRESS"

    save_report(path, report)

    print(f"SSM_COMMAND_ID={command_id}", flush=True)

    return poll(report, path)


def resume(path):
    report = json.loads(Path(path).read_text())

    validate_target(
        report["instance_id"],
        report["document_version"],
        report["release_sha"],
    )

    if report["document_name"] != DOCUMENT:
        raise ValueError("Unexpected document")

    uuid.UUID(report["command_id"])

    # Poll the existing command. Never submit another.
    report.pop("reason", None)

    return poll(report, path)


if __name__ == "__main__":
    try:
        if len(sys.argv) == 6 and sys.argv[1] == "send":
            sys.exit(execute(*sys.argv[2:]))

        if len(sys.argv) == 3 and sys.argv[1] == "resume":
            sys.exit(resume(sys.argv[2]))

        raise ValueError(
            "Usage: ssm_executor.py send INSTANCE VERSION SHA REPORT "
            "| resume REPORT"
        )

    except Exception as error:
        print(
            f"SSM EXECUTOR ERROR: {error}",
            file=sys.stderr,
        )
        sys.exit(22)
