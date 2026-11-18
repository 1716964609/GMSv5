#!/usr/bin/env python3

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

ARTIFACT = "gmsv5-linux-arm64.tar.gz"
CHECKSUM = ARTIFACT + ".sha256"
MANIFEST = "manifest.json"

MIGRATIONS = (
    Path(__file__).resolve().parents[2]
    / "src/lib/db/migrations.ts"
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def valid_sha(value, length):
    return (
        isinstance(value, str)
        and re.fullmatch(
            r"[0-9a-f]{" + str(length) + r"}", value
        ) is not None
    )


def verify_checksum(directory):
    artifact = directory / ARTIFACT
    checksum = directory / CHECKSUM

    for path in (artifact, checksum):
        require(
            path.is_file() and not path.is_symlink(),
            "Missing or invalid artifact file: " + path.name,
        )

    digest = sha256(artifact)

    expected_line = f"{digest}  {ARTIFACT}"

    require(
        checksum.read_text().strip() == expected_line,
        "Artifact SHA256 mismatch",
    )

    return digest


def create_manifest(directory, commit_sha):
    require(valid_sha(commit_sha, 40), "Invalid commit SHA")

    require(
        platform.system() == "Linux"
        and platform.machine() in ("aarch64", "arm64"),
        "Release packaging requires Linux ARM64",
    )

    node_version = subprocess.check_output(
        ["node", "--version"], text=True
    ).strip()

    manifest = {
        "manifest_version": 1,
        "commit_sha": commit_sha,
        "artifact": ARTIFACT,
        "artifact_sha256": verify_checksum(directory),
        "architecture": "arm64",
        "os": "linux",
        "node_version": node_version,
        "database_schema_fingerprint": sha256(MIGRATIONS),
        "workflow_run_id": os.environ["GITHUB_RUN_ID"],
        "workflow_run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
    }

    with (directory / MANIFEST).open("x") as file:
        json.dump(manifest, file, indent=2)
        file.write("\n")

    return verify_manifest(directory, commit_sha)


def verify_manifest(directory, expected_sha):
    require(valid_sha(expected_sha, 40), "Invalid expected SHA")

    path = directory / MANIFEST

    require(
        path.is_file() and not path.is_symlink(),
        "Invalid manifest file",
    )

    require(path.stat().st_size <= 16384, "Manifest too large")

    manifest = json.loads(path.read_text())

    require(isinstance(manifest, dict), "Invalid manifest")
    require(manifest.get("manifest_version") == 1,
            "Unsupported manifest version")
    require(manifest.get("commit_sha") == expected_sha,
            "Commit SHA mismatch")
    require(manifest.get("artifact") == ARTIFACT,
            "Unexpected artifact filename")
    require(manifest.get("architecture") == "arm64",
            "Architecture mismatch")
    require(manifest.get("os") == "linux",
            "Operating system mismatch")

    node = manifest.get("node_version")

    require(
        isinstance(node, str)
        and re.fullmatch(r"v26\.\d+\.\d+", node) is not None,
        "Unsupported Node.js version",
    )

    require(
        valid_sha(manifest.get("database_schema_fingerprint"), 64),
        "Invalid database schema fingerprint",
    )

    for field in ("workflow_run_id", "workflow_run_attempt"):
        value = manifest.get(field)

        require(
            isinstance(value, str) and value.isdigit(),
            "Invalid " + field,
        )

    digest = verify_checksum(directory)

    require(
        manifest.get("artifact_sha256") == digest,
        "Manifest SHA256 mismatch",
    )

    return manifest


def main():
    if len(sys.argv) != 4:
        raise ValueError(
            "Usage: artifact.py create|verify DIRECTORY COMMIT_SHA"
        )

    command, directory, commit_sha = sys.argv[1:]
    directory = Path(directory)

    if command == "create":
        result = create_manifest(directory, commit_sha)

    elif command == "verify":
        result = verify_manifest(directory, commit_sha)

    else:
        raise ValueError("Unknown command")

    print(json.dumps(result, indent=2))
    print("ARTIFACT VERIFICATION: PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ARTIFACT VERIFICATION: FAIL: {error}",
              file=sys.stderr)
        sys.exit(1)
