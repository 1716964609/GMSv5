import json
import os
import subprocess
import sys
import tempfile
import unittest

from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import state

OLD = "a" * 40
NEW = "b" * 40
FINGERPRINT = "c" * 64


class TransactionTests(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        self.root = Path(temp.name)
        self.directory = self.root / "state"
        self.directory.mkdir()

        releases = self.root / "releases"
        releases.mkdir()

        for sha in (OLD, NEW):
            release = releases / sha
            release.mkdir()

            (release / "server.js").write_text("test")

            (release / "manifest.json").write_text(
                json.dumps({
                    "manifest_version": 1,
                    "commit_sha": sha,
                    "database_schema_fingerprint":
                        FINGERPRINT,
                })
            )

        (self.root / "current").symlink_to(
            "releases/" + OLD
        )

        self.adapter = self.root / "adapter.sh"

        self.adapter.write_text(
            '''#!/usr/bin/env bash
set -euo pipefail

ACTION="$1"
SHA="$2"

ACTIVE="$(readlink "$TEST_ROOT/current")"
test "$ACTIVE" = "releases/$SHA"

echo "$ACTION $SHA" >> "$TEST_ROOT/events"

if [ "$ACTION" = restart ]; then
    if [ "$SHA" = "$NEW_SHA" ]; then
        touch "$TEST_ROOT/new-attempted"

        if [ "$FAILURE" = restart ]; then
            exit 1
        fi
    fi
fi

if [ "$ACTION" = health ]; then
    if [ "$SHA" = "$NEW_SHA" ]; then
        if [ "$FAILURE" = health ] ||
           [ "$FAILURE" = rollback ]; then
            exit 1
        fi
    else
        if [ "$FAILURE" = baseline ]; then
            exit 1
        fi

        if [ "$FAILURE" = rollback ] &&
           [ -e "$TEST_ROOT/new-attempted" ]; then
            exit 1
        fi
    fi
fi

exit 0
'''
        )

        self.adapter.chmod(0o755)

    def run_transaction(
        self, mode="deploy", failure="none"
    ):
        args = [
            "bash",
            str(SCRIPT_DIR / "transaction.sh"),
            mode,
            str(self.root),
            str(self.directory),
            str(self.adapter),
        ]

        if mode == "deploy":
            args.append(NEW)

        env = {
            **os.environ,
            "TEST_ROOT": str(self.root),
            "NEW_SHA": NEW,
            "FAILURE": failure,
        }

        return subprocess.run(
            args,
            env=env,
            text=True,
            capture_output=True,
            timeout=20,
        )

    def phase(self):
        return state.read_state(
            self.directory
        )["phase"]

    def current(self):
        return state.current_release(self.root)

    def assert_exit(self, result, expected):
        self.assertEqual(
            result.returncode,
            expected,
            result.stdout + result.stderr,
        )

    def prepare_interrupted(self):
        state.begin(self.directory, OLD, NEW)
        state.transition(self.directory, "VERIFIED")
        state.transition(self.directory, "SWITCHING")

    def test_successful_deployment(self):
        result = self.run_transaction()

        self.assert_exit(result, 0)
        self.assertEqual(self.phase(), "SUCCEEDED")
        self.assertEqual(self.current(), NEW)

    def test_restart_failure_rolls_back(self):
        result = self.run_transaction(
            failure="restart"
        )

        self.assert_exit(result, 20)
        self.assertEqual(self.phase(), "ROLLED_BACK")
        self.assertEqual(self.current(), OLD)

    def test_unhealthy_release_rolls_back(self):
        result = self.run_transaction(
            failure="health"
        )

        self.assert_exit(result, 20)
        self.assertEqual(self.phase(), "ROLLED_BACK")
        self.assertEqual(self.current(), OLD)

    def test_failed_rollback_blocks_deployment(self):
        result = self.run_transaction(
            failure="rollback"
        )

        self.assert_exit(result, 21)
        self.assertEqual(
            self.phase(), "ROLLBACK_FAILED"
        )

        blocked = self.run_transaction()
        self.assert_exit(blocked, 22)

    def test_unhealthy_baseline_is_preserved(self):
        result = self.run_transaction(
            failure="baseline"
        )

        self.assert_exit(result, 10)
        self.assertEqual(self.current(), OLD)
        self.assertFalse(
            (self.directory / "state.json").exists()
        )

    def test_schema_change_blocks_switch(self):
        path = (
            self.root / "releases" /
            NEW / "manifest.json"
        )

        manifest = json.loads(path.read_text())

        manifest["database_schema_fingerprint"] = (
            "d" * 64
        )

        path.write_text(json.dumps(manifest))

        result = self.run_transaction()

        self.assert_exit(result, 10)
        self.assertEqual(
            self.phase(), "FAILED_BEFORE_SWITCH"
        )
        self.assertEqual(self.current(), OLD)

    def test_interrupted_before_switch(self):
        state.begin(self.directory, OLD, NEW)

        result = self.run_transaction("recover")

        self.assert_exit(result, 0)
        self.assertEqual(
            self.phase(), "FAILED_BEFORE_SWITCH"
        )
        self.assertEqual(self.current(), OLD)

    def test_interrupted_after_switch(self):
        self.prepare_interrupted()

        state.switch_release(
            self.directory, self.root
        )

        self.assertEqual(self.current(), NEW)

        result = self.run_transaction("recover")

        self.assert_exit(result, 0)
        self.assertEqual(self.phase(), "ROLLED_BACK")
        self.assertEqual(self.current(), OLD)

    def test_recovery_from_rollback_failure(self):
        failed = self.run_transaction(
            failure="rollback"
        )

        self.assert_exit(failed, 21)

        recovered = self.run_transaction("recover")

        self.assert_exit(recovered, 0)
        self.assertEqual(self.phase(), "ROLLED_BACK")
        self.assertEqual(self.current(), OLD)

    def test_redeploy_same_release_is_noop(self):
        self.assert_exit(self.run_transaction(), 0)

        second = self.run_transaction()

        self.assert_exit(second, 0)
        self.assertIn(
            "ALREADY_DEPLOYED", second.stdout
        )
        self.assertEqual(self.current(), NEW)


if __name__ == "__main__":
    unittest.main()
