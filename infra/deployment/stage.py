#!/usr/bin/env python3

import json
import os
import shutil
import sys
import tempfile

from pathlib import Path

from artifact import (
    ARTIFACT,
    MANIFEST,
    verify_manifest,
)
from safe_extract import (
    MAX_BYTES,
    extract_archive,
)
from state import (
    sync_directory,
    verify_release,
)

MIN_FREE_BYTES = MAX_BYTES + 256 * 1024 ** 2


def sync_tree(root):
    for directory, _, files in os.walk(
        root, topdown=False, followlinks=False
    ):
        directory = Path(directory)

        for name in files:
            path = directory / name

            if path.is_symlink():
                continue

            with path.open("rb") as file:
                os.fsync(file.fileno())

        sync_directory(directory)


def stage_release(root, source, sha):
    root = Path(root)
    source = Path(source)

    manifest = verify_manifest(source, sha)

    releases = root / "releases"

    if not releases.is_dir() or releases.is_symlink():
        raise ValueError("Invalid releases directory")

    destination = releases / sha

    # A published release must never be overwritten.
    if os.path.lexists(destination):
        verify_release(root, sha)

        existing = destination / MANIFEST

        if not existing.is_file() or existing.is_symlink():
            raise ValueError("Missing existing release manifest")

        if json.loads(existing.read_text()) != manifest:
            raise ValueError("Existing release manifest mismatch")

        print("RELEASE ALREADY STAGED")
        return destination

    if shutil.disk_usage(releases).free < MIN_FREE_BYTES:
        raise ValueError("Insufficient deployment disk space")

    with tempfile.TemporaryDirectory(
        prefix=".staging-",
        dir=releases,
    ) as temporary:

        staged = Path(temporary) / "payload"

        extract_archive(
            source / ARTIFACT,
            staged,
        )

        # Verify the archive again after extraction.
        if verify_manifest(source, sha) != manifest:
            raise ValueError("Artifact changed during staging")

        # Manifest is deployment-controlled metadata.
        with (staged / MANIFEST).open("x") as file:
            json.dump(manifest, file, indent=2)
            file.write("\n")

        sync_tree(staged)

        if os.path.lexists(destination):
            raise ValueError("Release destination already exists")

        os.rename(staged, destination)

        sync_directory(Path(temporary))
        sync_directory(releases)

    verify_release(root, sha)

    print(f"RELEASE STAGED sha={sha}")
    return destination


if __name__ == "__main__":
    try:
        if len(sys.argv) != 4:
            raise ValueError(
                "Usage: stage.py ROOT ARTIFACT_DIRECTORY SHA"
            )

        result = stage_release(*sys.argv[1:])
        print(result)

    except Exception as error:
        print(
            f"RELEASE STAGING FAILED: {error}",
            file=sys.stderr,
        )
        sys.exit(1)
