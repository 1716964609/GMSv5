#!/usr/bin/env python3

import os
import shutil
import sys
import tarfile

from pathlib import Path, PurePosixPath

MAX_FILES = 100_000
MAX_BYTES = 2 * 1024 ** 3


def require(condition, message):
    if not condition:
        raise ValueError(message)


def extract_archive(archive, destination):
    destination = Path(destination)

    # Never extract over an existing release.
    destination.mkdir(mode=0o700, exist_ok=False)

    try:
        root = destination.resolve()

        with tarfile.open(archive, "r:gz") as tar:
            entries = {}
            total_bytes = 0

            for member in tar:
                path = PurePosixPath(member.name)

                require(
                    not path.is_absolute()
                    and ".." not in path.parts
                    and "\\" not in member.name,
                    f"Unsafe archive path: {member.name}",
                )

                if not path.parts:
                    require(member.isdir(), "Invalid archive root")
                    continue

                require(
                    path not in entries,
                    f"Duplicate archive entry: {path}",
                )

                require(
                    member.isdir()
                    or member.isfile()
                    or member.issym(),
                    f"Unsupported archive entry: {path}",
                )

                entries[path] = member

                require(
                    len(entries) <= MAX_FILES,
                    "Too many archive entries",
                )

                if member.isfile():
                    total_bytes += member.size

                    require(
                        total_bytes <= MAX_BYTES,
                        "Archive exceeds expanded size limit",
                    )

                if member.issym():
                    target = PurePosixPath(member.linkname)

                    require(
                        not target.is_absolute()
                        and "\\" not in member.linkname
                        and bool(member.linkname),
                        f"Unsafe symlink: {path}",
                    )

            # Reject files nested below symlinks or regular files.
            for path in entries:
                for parent in path.parents:
                    if parent in entries:
                        require(
                            entries[parent].isdir(),
                            f"Unsafe parent: {parent}",
                        )

            # Extract directories and regular files first.
            for path, member in entries.items():
                target = root.joinpath(*path.parts)

                if member.isdir():
                    target.mkdir(
                        parents=True, exist_ok=True
                    )

                elif member.isfile():
                    target.parent.mkdir(
                        parents=True, exist_ok=True
                    )

                    with tar.extractfile(member) as source:
                        with target.open("xb") as output:
                            shutil.copyfileobj(source, output)

                    target.chmod(
                        0o755 if member.mode & 0o111
                        else 0o644
                    )

            # Create symlinks only after all file writes.
            for path, member in entries.items():
                if member.issym():
                    target = root.joinpath(*path.parts)
                    target.parent.mkdir(
                        parents=True, exist_ok=True
                    )
                    os.symlink(member.linkname, target)

            # Validate final symlink resolution.
            for path, member in entries.items():
                if member.issym():
                    resolved = root.joinpath(
                        *path.parts
                    ).resolve(strict=True)

                    require(
                        resolved.is_relative_to(root),
                        f"Symlink escapes release: {path}",
                    )

        require(
            (root / "server.js").is_file()
            and not (root / "server.js").is_symlink(),
            "Missing standalone server.js",
        )

        require(
            (root / ".next").is_dir()
            and not (root / ".next").is_symlink(),
            "Missing .next directory",
        )

        destination.chmod(0o755)

    except BaseException:
        shutil.rmtree(destination)
        raise


if __name__ == "__main__":
    try:
        if len(sys.argv) != 3:
            raise ValueError(
                "Usage: safe_extract.py ARCHIVE DESTINATION"
            )

        extract_archive(sys.argv[1], sys.argv[2])

        print("SAFE EXTRACTION: PASS")

    except Exception as error:
        print(f"SAFE EXTRACTION: FAIL: {error}",
              file=sys.stderr)
        sys.exit(1)
