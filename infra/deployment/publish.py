#!/usr/bin/env python3

import json
import re
import subprocess
import sys
import tempfile

from pathlib import Path

from artifact import (
    ARTIFACT,
    CHECKSUM,
    MANIFEST,
    sha256,
    verify_manifest,
)

from download import aws, SIZE_LIMITS


def verify_existing(bucket, key, source):
    metadata = json.loads(
        aws(
            "head-object",
            "--bucket", bucket,
            "--key", key,
        )
    )

    if metadata["ContentLength"] != source.stat().st_size:
        raise ValueError(
            f"Existing object size conflict: {key}"
        )

    with tempfile.TemporaryDirectory() as temp:
        destination = Path(temp) / "existing"

        aws(
            "get-object",
            "--bucket", bucket,
            "--key", key,
            "--if-match", metadata["ETag"],
            str(destination),
        )

        if sha256(destination) != sha256(source):
            raise ValueError(
                f"Existing object checksum conflict: {key}"
            )


def publish_object(bucket, key, source):
    try:
        aws(
            "put-object",
            "--bucket", bucket,
            "--key", key,
            "--body", str(source),
            "--if-none-match", "*",
            "--checksum-algorithm", "SHA256",
            "--server-side-encryption", "AES256",
        )

        print(
            f"OBJECT_PUBLISHED key={key}",
            flush=True,
        )

    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ):
        # A previous attempt may already have written
        # the object. Never overwrite it.

        verify_existing(bucket, key, source)

        print(
            f"IDENTICAL_OBJECT_REUSED key={key}",
            flush=True,
        )


def publish_release(bucket, directory, commit_sha):
    directory = Path(directory)

    if not re.fullmatch(
        r"gmsv5-releases-[0-9]{12}-apne1",
        bucket,
    ):
        raise ValueError("Invalid release bucket")

    manifest = verify_manifest(
        directory, commit_sha
    )

    filenames = (ARTIFACT, CHECKSUM, MANIFEST)

    # Validate every local file before uploading anything.
    for filename in filenames:
        path = directory / filename

        if not 0 < path.stat().st_size <= SIZE_LIMITS[filename]:
            raise ValueError(
                f"Invalid publication size: {filename}"
            )

    # Manifest is published last.
    for filename in filenames:
        publish_object(
            bucket,
            f"releases/{commit_sha}/{filename}",
            directory / filename,
        )

    print(
        f"RELEASE_PUBLISHED sha={commit_sha} "
        f"digest={manifest['artifact_sha256']}",
        flush=True,
    )

    return manifest


if __name__ == "__main__":
    try:
        if len(sys.argv) != 4:
            raise ValueError(
                "Usage: publish.py BUCKET DIRECTORY COMMIT_SHA"
            )

        publish_release(*sys.argv[1:])

    except Exception as error:
        print(
            f"RELEASE PUBLICATION FAILED: {error}",
            file=sys.stderr,
        )

        sys.exit(1)
