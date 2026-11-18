#!/usr/bin/env python3

import os
import subprocess
import sys

from pathlib import Path

import preflight
import state

ROOT = Path("/opt/gmsv5")
STATE_DIR = Path("/var/lib/gmsv5-deploy")
ADAPTER = ROOT / "deploy/systemd-adapter.sh"


def prepare(root, directory):
    directory = Path(directory)
    active = state.current_release(root)

    if not os.path.lexists(directory / "state.json"):
        print("BOOT: No deployment transaction")
        return

    transaction = state.read_state(directory)

    phase = transaction["phase"]
    previous = transaction["previous"]
    target = transaction["target"]

    if phase in state.TERMINAL:
        expected = (
            target if phase == "SUCCEEDED"
            else previous
        )

        if active != expected:
            raise ValueError(
                "Current release disagrees with terminal state"
            )

        print(f"BOOT: Terminal state verified phase={phase}")
        return

    if phase in ("PREPARING", "VERIFIED"):
        if active != previous:
            raise ValueError(
                "Unexpected release before switch"
            )

        state.transition(
            directory,
            "FAILED_BEFORE_SWITCH",
            "boot_interrupted_before_switch",
        )

        print("BOOT: Previous release preserved")
        return

    if phase in (
        "SWITCHING",
        "VALIDATING",
        "ROLLING_BACK",
        "ROLLBACK_FAILED",
    ):
        if phase != "ROLLING_BACK":
            state.transition(
                directory,
                "ROLLING_BACK",
                "boot_interrupted_deployment",
            )

        try:
            state.switch_release(directory, root)

        except Exception:
            state.transition(
                directory,
                "ROLLBACK_FAILED",
                "boot_release_switch_failed",
            )
            raise

        print(f"BOOT: Previous release selected sha={previous}")
        return

    raise ValueError("Unsupported deployment state")


def verify(root, directory, health):
    directory = Path(directory)

    if not os.path.lexists(directory / "state.json"):
        print("BOOT: No recovery to verify")
        return True

    transaction = state.read_state(directory)
    phase = transaction["phase"]

    if phase in state.TERMINAL:
        print(f"BOOT: No recovery required phase={phase}")
        return True

    if phase != "ROLLING_BACK":
        raise ValueError(
            "Unexpected state during boot verification"
        )

    previous = transaction["previous"]

    try:
        ready = (
            state.current_release(root) == previous
            and health(previous)
        )

    except Exception:
        ready = False

    if ready:
        state.transition(
            directory,
            "ROLLED_BACK",
            "boot_recovery_verified",
        )

        print(f"BOOT_RECOVERY_SUCCEEDED sha={previous}")
        return True

    state.transition(
        directory,
        "ROLLBACK_FAILED",
        "boot_recovery_health_failed",
    )

    print(f"BOOT_RECOVERY_FAILED sha={previous}")
    return False


def production_health(sha):
    result = subprocess.run(
        [str(ADAPTER), "health", sha],
        timeout=150,
    )

    return result.returncode == 0


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2:
        raise ValueError("Root and recovery action required")

    for path in (
        ROOT,
        ROOT / "releases",
        ROOT / "deploy",
        STATE_DIR,
    ):
        preflight.trusted_path(path, directory=True)

    config = preflight.load_config()

    preflight.verify_mount(
        config["data_filesystem_uuid"]
    )

    if sys.argv[1] == "guard":
        active = state.current_release(ROOT)

        preflight.trusted_path(
            ROOT / "releases" / active,
            directory=True,
        )

        print(f"STARTUP GUARD: PASS release={active}")
        return 0

    if sys.argv[1] == "prepare":
        prepare(ROOT, STATE_DIR)
        return 0

    if sys.argv[1] == "verify":
        return (
            0 if verify(
                ROOT, STATE_DIR, production_health
            )
            else 21
        )

    raise ValueError("Unknown recovery action")


if __name__ == "__main__":
    try:
        sys.exit(main())

    except Exception as error:
        print(
            f"BOOT RECOVERY ERROR: {error}",
            file=sys.stderr,
        )
        sys.exit(22)
