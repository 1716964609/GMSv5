#!/usr/bin/env python3

import json
import os
import re
import sys
import tempfile
import uuid

from pathlib import Path

from download import aws
from state import validate_sha, timestamp
from ssm_executor import execute

LOCK_KEY = "deployment-control/lock.json"

SAFE_TO_RELEASE = {
    (0, "SUCCEEDED"),
    (10, "FAILED_BEFORE_SWITCH"),
    (20, "ROLLED_BACK"),
}


def acquire(bucket, sha):
    validate_sha(sha)

    if not re.fullmatch(
        r"gmsv5-releases-[0-9]{12}-apne1", bucket
    ):
        raise ValueError("Invalid bucket")

    lock = {
        "version": 1,
        "request_id": uuid.uuid4().hex,
        "release_sha": sha,
        "run_id": os.environ.get("GITHUB_RUN_ID", "manual"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
        "created_at": timestamp(),
    }

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "lock.json"

        path.write_text(json.dumps(lock, sort_keys=True))

        result = json.loads(aws(
            "put-object",
            "--bucket", bucket,
            "--key", LOCK_KEY,
            "--body", str(path),
            "--if-none-match", "*",
            "--server-side-encryption", "AES256",
        ))

    etag = result["ETag"]

    if not re.fullmatch(r'"[a-f0-9]{32}"', etag):
        raise ValueError("Unexpected lock ETag")

    print(
        f"DEPLOYMENT_LOCK_ACQUIRED "
        f"request_id={lock['request_id']} "
        f"etag={etag}",
        flush=True,
    )

    return lock, etag


def release(bucket, etag):
    if not re.fullmatch(r'"[a-f0-9]{32}"', etag):
        raise ValueError("Invalid lock ETag")

    aws(
        "delete-object",
        "--bucket", bucket,
        "--key", LOCK_KEY,
        "--if-match", etag,
    )

    print("DEPLOYMENT_LOCK_RELEASED", flush=True)


def deploy(bucket, instance, version, sha, report):
    lock, etag = acquire(bucket, sha)

    # Never release the lock from a finally block.
    # Unexpected termination must leave the lock intact.
    code = execute(
        instance,
        version,
        sha,
        report,
        request_id=lock["request_id"],
    )

    result = json.loads(Path(report).read_text())

    if (
        result.get("request_id") != lock["request_id"]
        or result.get("release_sha") != sha
        or result.get("instance_id") != instance
    ):
        raise ValueError("Deployment report identity mismatch")

    if (code, result.get("outcome")) in SAFE_TO_RELEASE:
        release(bucket, etag)
    else:
        print(
            "DEPLOYMENT_LOCK_RETAINED: "
            "operator reconciliation required",
            flush=True,
        )

    return code


if __name__ == "__main__":
    try:
        if len(sys.argv) != 6:
            raise ValueError(
                "Usage: deployment_lock.py "
                "BUCKET INSTANCE VERSION SHA REPORT"
            )

        sys.exit(deploy(*sys.argv[1:]))

    except Exception as error:
        print(
            f"DEPLOYMENT LOCK ERROR: {error}",
            file=sys.stderr,
        )
        sys.exit(22)
