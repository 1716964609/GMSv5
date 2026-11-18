#!/usr/bin/env python3

import fcntl
import json
import os
import re
import sys
import tempfile
import uuid

from datetime import datetime, timezone
from pathlib import Path


TRANSITIONS = {
    "PREPARING": {"VERIFIED", "FAILED_BEFORE_SWITCH"},
    "VERIFIED": {"SWITCHING", "FAILED_BEFORE_SWITCH"},
    "SWITCHING": {"VALIDATING", "ROLLING_BACK"},
    "VALIDATING": {"SUCCEEDED", "ROLLING_BACK"},
    "ROLLING_BACK": {"ROLLED_BACK", "ROLLBACK_FAILED"},
    "ROLLBACK_FAILED": {"ROLLING_BACK"},
    "FAILED_BEFORE_SWITCH": set(),
    "SUCCEEDED": set(),
    "ROLLED_BACK": set(),
}

TERMINAL = {
    "SUCCEEDED",
    "ROLLED_BACK",
    "FAILED_BEFORE_SWITCH",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def validate_sha(sha):
    require(
        isinstance(sha, str)
        and re.fullmatch(r"[0-9a-f]{40}", sha)
        is not None,
        "Invalid release SHA",
    )


def sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY)

    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_state(directory, state, filename="state.json"):
    directory = Path(directory)

    fd, temporary_name = tempfile.mkstemp(
        prefix=".state-",
        dir=directory,
    )

    temporary = Path(temporary_name)

    try:
        with os.fdopen(fd, "w") as file:
            json.dump(state, file, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        os.replace(
            temporary,
            directory / filename,
        )

        sync_directory(directory)

    finally:
        temporary.unlink(missing_ok=True)


def read_state(directory):
    path = Path(directory) / "state.json"

    require(
        path.is_file() and not path.is_symlink(),
        "Deployment state is missing or invalid",
    )

    state = json.loads(path.read_text())

    require(
        state.get("version") == 1,
        "Unsupported state version",
    )

    require(
        state.get("phase") in TRANSITIONS,
        "Unknown deployment state",
    )

    validate_sha(state.get("previous"))
    validate_sha(state.get("target"))

    require(
        state["previous"] != state["target"],
        "Invalid release transaction",
    )

    return state


def archive_terminal_state(directory, state):
    require(
        state["phase"] in TERMINAL,
        "Cannot archive unfinished transaction",
    )

    transaction_id = state.get("transaction_id")

    require(
        isinstance(transaction_id, str)
        and re.fullmatch(r"[0-9a-f]{32}", transaction_id)
        is not None,
        "Invalid transaction ID",
    )

    directory = Path(directory)
    history = directory / "history"

    history.mkdir(mode=0o700, exist_ok=True)

    require(
        history.is_dir() and not history.is_symlink(),
        "Invalid history directory",
    )

    sync_directory(directory)

    filename = transaction_id + ".json"
    path = history / filename

    if os.path.lexists(path):
        require(
            path.is_file() and not path.is_symlink(),
            "Invalid history entry",
        )

        require(
            json.loads(path.read_text()) == state,
            "History transaction conflict",
        )

        return

    write_state(history, state, filename)


def begin(directory, previous, target):
    directory = Path(directory)

    validate_sha(previous)
    validate_sha(target)

    require(previous != target, "Already deployed")

    if os.path.lexists(directory / "state.json"):
        old = read_state(directory)

        require(
            old["phase"] in TERMINAL,
            "Unresolved deployment transaction",
        )

        archive_terminal_state(directory, old)

    now = timestamp()

    state = {
        "version": 1,
        "transaction_id": uuid.uuid4().hex,
        "phase": "PREPARING",
        "previous": previous,
        "target": target,
        "started_at": now,
        "updated_at": now,
        "reason": "",
    }

    write_state(directory, state)

    return state


def transition(directory, phase, reason=""):
    state = read_state(directory)
    old_phase = state["phase"]

    require(
        phase in TRANSITIONS[old_phase],
        f"Invalid transition: {old_phase} -> {phase}",
    )

    state["phase"] = phase
    state["reason"] = reason
    state["updated_at"] = timestamp()

    write_state(directory, state)

    return state


def verify_release(root, sha):
    validate_sha(sha)

    releases = Path(root) / "releases"
    release = releases / sha

    require(
        releases.is_dir()
        and not releases.is_symlink(),
        "Invalid releases directory",
    )

    require(
        release.is_dir()
        and not release.is_symlink(),
        "Release directory missing or invalid",
    )

    require(
        (release / "server.js").is_file()
        and not (release / "server.js").is_symlink(),
        "Release server.js missing or invalid",
    )


def current_release(root):
    root = Path(root)
    link = root / "current"

    require(
        link.is_symlink(),
        "Current is not a symbolic link",
    )

    target = os.readlink(link)

    require(
        re.fullmatch(
            r"releases/[0-9a-f]{40}", target
        ) is not None,
        "Invalid current release link",
    )

    sha = target.split("/")[1]

    verify_release(root, sha)

    return sha


def switch_release(directory, root):
    state = read_state(directory)
    root = Path(root)

    if state["phase"] == "SWITCHING":
        desired = state["target"]

    elif state["phase"] == "ROLLING_BACK":
        desired = state["previous"]

    else:
        raise ValueError(
            "Release switch not allowed in current phase"
        )

    current = current_release(root)

    require(
        current in (state["previous"], state["target"]),
        "Current release is outside transaction",
    )

    verify_release(root, desired)

    if current == desired:
        return desired

    # The temporary link and current are on the same filesystem.
    with tempfile.TemporaryDirectory(
        prefix=".switch-",
        dir=root,
    ) as temp:
        link = Path(temp) / "current"

        link.symlink_to("releases/" + desired)

        os.replace(link, root / "current")
        sync_directory(root)

    return desired


def run_with_lock(directory, command):
    require(bool(command), "Missing locked command")

    lock = Path(directory) / "deploy.lock"

    fd = os.open(
        lock,
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )

    try:
        fcntl.flock(
            fd, fcntl.LOCK_EX | fcntl.LOCK_NB
        )

    except BlockingIOError:
        os.close(fd)

        raise ValueError(
            "Another deployment is already running"
        )

    # Preserve the lock across exec into the Bash orchestrator.
    os.set_inheritable(fd, True)
    os.execvp(command[0], command)


def main():
    action = sys.argv[1]
    directory = Path(sys.argv[2])
    args = sys.argv[3:]

    if action == "begin" and len(args) == 2:
        result = begin(directory, *args)

    elif action == "transition" and len(args) in (1, 2):
        result = transition(directory, *args)

    elif action == "show" and not args:
        result = read_state(directory)

    elif action == "current" and len(args) == 1:
        print(current_release(args[0]))
        return

    elif action == "switch" and len(args) == 1:
        print(switch_release(directory, args[0]))
        return

    elif action == "lock":
        run_with_lock(directory, args)
        return

    else:
        raise ValueError("Invalid state command")

    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            f"DEPLOYMENT STATE ERROR: {error}",
            file=sys.stderr,
        )
        sys.exit(1)
