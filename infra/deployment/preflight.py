#!/usr/bin/env python3

import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys

from pathlib import Path

from state import current_release

ROOT = Path("/opt/gmsv5")
STATE_DIR = Path("/var/lib/gmsv5-deploy")
MOUNT = Path("/mnt/gms-data")
DATABASE = MOUNT / "gmsv5.sqlite"

CONFIG = Path("/etc/gmsv5-deploy.json")

MIN_FREE_BYTES = 3 * 1024 ** 3


def require(condition, message):
    if not condition:
        raise ValueError(message)


def command(*args):
    return subprocess.check_output(
        args, text=True, timeout=15
    ).strip()


def trusted_path(path, directory=False):
    path = Path(path)

    require(
        not path.is_symlink(),
        f"Unexpected symlink: {path}",
    )

    info = path.stat()

    require(
        info.st_uid == 0,
        f"Path must be root-owned: {path}",
    )

    require(
        not info.st_mode & 0o022,
        f"Unsafe write permissions: {path}",
    )

    require(
        stat.S_ISDIR(info.st_mode)
        if directory
        else stat.S_ISREG(info.st_mode),
        f"Unexpected path type: {path}",
    )


def load_config():
    trusted_path(CONFIG)

    config = json.loads(CONFIG.read_text())

    require(
        re.fullmatch(
            r"gmsv5-releases-[0-9]{12}-apne1",
            config["bucket"],
        ) is not None,
        "Invalid configured bucket",
    )

    require(
        bool(config["data_filesystem_uuid"]),
        "Missing data filesystem UUID",
    )

    require(
        Path(config["node_binary"]).is_absolute(),
        "Node binary must use an absolute path",
    )

    return config


def verify_mount(expected_uuid):
    actual_uuid = command(
        "findmnt",
        "--mountpoint", str(MOUNT),
        "--noheadings",
        "--raw",
        "--output", "UUID",
    )

    require(
        actual_uuid == expected_uuid,
        "Data filesystem UUID mismatch",
    )

    require(
        DATABASE.is_file()
        and not DATABASE.is_symlink(),
        "Database missing or invalid",
    )

    require(
        DATABASE.stat().st_dev == MOUNT.stat().st_dev,
        "Database is outside expected filesystem",
    )

    for path, access in (
        (DATABASE, "-r"),
        (DATABASE, "-w"),
        (MOUNT, "-w"),
    ):
        subprocess.run(
            [
                "runuser", "-u", "gmsv5", "--",
                "test", access, str(path),
            ],
            check=True,
            timeout=10,
        )


def preflight():
    require(os.geteuid() == 0, "Root required")

    require(
        platform.system() == "Linux"
        and platform.machine() in ("aarch64", "arm64"),
        "Unsupported production platform",
    )

    config = load_config()

    for directory in (
        ROOT,
        ROOT / "releases",
        ROOT / "deploy",
        STATE_DIR,
    ):
        trusted_path(directory, directory=True)

    require(
        shutil.disk_usage(ROOT / "releases").free
        >= MIN_FREE_BYTES,
        "Insufficient free disk space",
    )

    node_version = command(
        config["node_binary"], "--version"
    )

    require(
        re.fullmatch(
            r"v26\.\d+\.\d+", node_version
        ) is not None,
        "Unexpected Node.js runtime",
    )

    verify_mount(config["data_filesystem_uuid"])

    working_directory = command(
        "systemctl", "show", "gmsv5.service",
        "--property=WorkingDirectory", "--value",
    )

    require(
        working_directory == str(ROOT / "current"),
        "Service is not using managed current path",
    )

    service_user = command(
        "systemctl", "show", "gmsv5.service",
        "--property=User", "--value",
    )

    require(
        service_user == "gmsv5",
        "Unexpected service user",
    )

    active = current_release(ROOT)

    trusted_path(
        ROOT / "releases" / active,
        directory=True,
    )

    return config


if __name__ == "__main__":
    try:
        result = preflight()

        print(result["bucket"])

    except Exception as error:
        print(
            f"PREFLIGHT FAILED: {error}",
            file=sys.stderr,
        )
        sys.exit(1)
