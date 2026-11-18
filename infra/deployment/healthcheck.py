#!/usr/bin/env python3

import http.client
import json
import re
import sys
import time


def probe(expected_sha, port=3000, timeout=2):
    connection = http.client.HTTPConnection(
        "127.0.0.1", port, timeout=timeout
    )

    try:
        connection.request(
            "GET",
            "/api/health/ready",
            headers={"Cache-Control": "no-cache"},
        )

        response = connection.getresponse()

        if response.status != 200:
            return False

        body = response.read(4097)

        if len(body) > 4096:
            return False

        result = json.loads(body)

        return (
            isinstance(result, dict)
            and result.get("status") == "ready"
            and result.get("release") == expected_sha
        )

    except (
        OSError,
        http.client.HTTPException,
        ValueError,
    ):
        return False

    finally:
        connection.close()


def wait_ready(
    expected_sha,
    port=3000,
    timeout=120,
    interval=3,
    consecutive=3,
    grace_period=5,
):
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ValueError("Invalid expected release SHA")

    if consecutive < 1 or timeout <= 0:
        raise ValueError("Invalid health check configuration")

    deadline = time.monotonic() + timeout
    successes = 0
    attempts = 0

    time.sleep(min(grace_period, timeout))

    while time.monotonic() < deadline:
        attempts += 1
        remaining = deadline - time.monotonic()

        if probe(
            expected_sha,
            port,
            timeout=min(2, remaining),
        ):
            successes += 1
        else:
            successes = 0

        print(
            f"HEALTH attempt={attempts} "
            f"consecutive_success={successes}/{consecutive}",
            flush=True,
        )

        if successes >= consecutive:
            return True

        remaining = deadline - time.monotonic()

        if remaining > 0:
            time.sleep(min(interval, remaining))

    return False


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError(
                "Usage: healthcheck.py COMMIT_SHA"
            )

        if wait_ready(sys.argv[1]):
            print("HEALTH CHECK: PASS")
            sys.exit(0)

        print("HEALTH CHECK: FAIL", file=sys.stderr)
        sys.exit(1)

    except Exception as error:
        print(
            f"HEALTH CHECK ERROR: {error}",
            file=sys.stderr,
        )
        sys.exit(1)
