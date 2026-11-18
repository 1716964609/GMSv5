#!/usr/bin/env python3

import json
import re
import sys

from pathlib import Path

from state import verify_release


def fingerprint(root, sha):
    verify_release(root, sha)

    path = Path(root) / "releases" / sha / "manifest.json"

    if not path.is_file() or path.is_symlink():
        raise ValueError("Missing release manifest")

    manifest = json.loads(path.read_text())

    if manifest.get("manifest_version") != 1:
        raise ValueError("Unsupported manifest version")

    if manifest.get("commit_sha") != sha:
        raise ValueError("Release identity mismatch")

    value = manifest.get("database_schema_fingerprint")

    if not isinstance(value, str):
        raise ValueError("Missing schema fingerprint")

    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Invalid schema fingerprint")

    return value


def verify_compatibility(root, previous, target):
    if fingerprint(root, previous) != fingerprint(root, target):
        raise ValueError(
            "Schema fingerprint changed; "
            "automatic deployment prohibited"
        )


if __name__ == "__main__":
    try:
        if len(sys.argv) != 4:
            raise ValueError(
                "Usage: compatibility.py ROOT PREVIOUS TARGET"
            )

        verify_compatibility(*sys.argv[1:])

        print("SCHEMA COMPATIBILITY: PASS")

    except Exception as error:
        print(
            f"SCHEMA COMPATIBILITY: FAIL: {error}",
            file=sys.stderr,
        )
        sys.exit(1)
