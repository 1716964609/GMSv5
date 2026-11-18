#!/usr/bin/env python3

import json
import os
import re
import subprocess
import sys

from pathlib import Path

from artifact import (
    ARTIFACT,
    CHECKSUM,
    MANIFEST,
    verify_manifest,
)
from state import validate_sha

REGION = "ap-northeast-1"

ENDPOINT = (
    "https://s3.dualstack.ap-northeast-1.amazonaws.com"
)

SIZE_LIMITS = {
    ARTIFACT: 512 * 1024 ** 2,
    CHECKSUM: 1024,
    MANIFEST: 16384,
}


def aws(*args):
    return subprocess.check_output(
        [
            "aws",
            "--region", REGION,
            "--endpoint-url", ENDPOINT,
            "--cli-connect-timeout", "10",
            "--cli-read-timeout", "30",
            "--output", "json",
            "s3api",
            *args,
        ],
        text=True,
        timeout=180,
        env={
            **os.environ,
            "AWS_PAGER": "",
            "AWS_RETRY_MODE": "standard",
            "AWS_MAX_ATTEMPTS": "3",
        },
    )


def download_release(bucket, sha, destination):
    validate_sha(sha)

    if not re.fullmatch(
        r"gmsv5-releases-[0-9]{12}-apne1",
        bucket,
    ):
        raise ValueError("Unexpected release bucket")

    destination = Path(destination)

    if (
        not destination.is_dir()
        or destination.is_symlink()
        or any(destination.iterdir())
    ):
        raise ValueError("Download directory must be empty")

    for filename in (MANIFEST, CHECKSUM, ARTIFACT):
        key = f"releases/{sha}/{filename}"

        metadata = json.loads(
            aws(
                "head-object",
                "--bucket", bucket,
                "--key", key,
            )
        )

        size = metadata["ContentLength"]

        if (
            type(size) is not int
            or not 0 < size <= SIZE_LIMITS[filename]
        ):
            raise ValueError(
                f"Invalid object size: {filename}"
            )

        target = destination / filename

        version_constraint = [
            "--if-match", metadata["ETag"],
        ]

        if metadata.get("VersionId"):
            version_constraint += [
                "--version-id", metadata["VersionId"],
            ]

        aws(
            "get-object",
            "--bucket", bucket,
            "--key", key,
            *version_constraint,
            str(target),
        )

        if (
            not target.is_file()
            or target.is_symlink()
            or target.stat().st_size != size
        ):
            raise ValueError(
                f"Downloaded size mismatch: {filename}"
            )

        print(
            f"DOWNLOAD_OK file={filename} bytes={size}",
            flush=True,
        )

    return verify_manifest(destination, sha)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 4:
            raise ValueError(
                "Usage: download.py BUCKET SHA DESTINATION"
            )

        download_release(*sys.argv[1:])

        print("RELEASE DOWNLOAD: PASS")

    except Exception as error:
        print(
            f"RELEASE DOWNLOAD FAILED: {error}",
            file=sys.stderr,
        )
        sys.exit(1)
