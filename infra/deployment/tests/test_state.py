import fcntl
import os
import subprocess
import sys
import tempfile
import unittest

from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)

import state

OLD = "a" * 40
NEW = "b" * 40
OTHER = "c" * 40


class StateTests(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        self.root = Path(temp.name)
        self.directory = self.root / "state"
        self.directory.mkdir()

        releases = self.root / "releases"
        releases.mkdir()

        for sha in (OLD, NEW, OTHER):
            release = releases / sha
            release.mkdir()
            (release / "server.js").write_text("test")

        (self.root / "current").symlink_to(
            "releases/" + OLD
        )

    def begin(self):
        return state.begin(self.directory, OLD, NEW)

    def move(self, phase):
        return state.transition(self.directory, phase)

    def phase(self):
        return state.read_state(self.directory)["phase"]

    def current(self):
        return state.current_release(self.root)

    def switch(self):
        return state.switch_release(
            self.directory, self.root
        )

    def prepare_switch(self):
        self.begin()
        self.move("VERIFIED")
        self.move("SWITCHING")

    def test_begin_persists_previous_and_target(self):
        self.begin()

        result = state.read_state(self.directory)

        self.assertEqual(result["previous"], OLD)
        self.assertEqual(result["target"], NEW)
        self.assertEqual(result["phase"], "PREPARING")
        self.assertEqual(self.current(), OLD)

    def test_cannot_skip_validation(self):
        self.begin()

        with self.assertRaises(ValueError):
            self.move("SUCCEEDED")

        self.assertEqual(self.phase(), "PREPARING")

    def test_active_transaction_blocks_new_deployment(self):
        self.begin()

        with self.assertRaises(ValueError):
            state.begin(self.directory, OLD, OTHER)

    def test_failure_before_switch_preserves_old_release(self):
        self.begin()
        self.move("FAILED_BEFORE_SWITCH")

        self.assertEqual(self.current(), OLD)

    def test_switch_requires_correct_phase(self):
        self.begin()

        with self.assertRaises(ValueError):
            self.switch()

        self.assertEqual(self.current(), OLD)

    def test_successful_deployment(self):
        self.prepare_switch()
        self.switch()

        self.assertEqual(self.current(), NEW)

        self.move("VALIDATING")
        self.move("SUCCEEDED")

        self.assertEqual(self.phase(), "SUCCEEDED")

    def test_automatic_rollback_switch(self):
        self.prepare_switch()
        self.switch()
        self.move("VALIDATING")
        self.move("ROLLING_BACK")
        self.switch()
        self.move("ROLLED_BACK")

        self.assertEqual(self.current(), OLD)
        self.assertEqual(self.phase(), "ROLLED_BACK")

    def test_switch_is_idempotent(self):
        self.prepare_switch()

        self.switch()
        self.switch()

        self.assertEqual(self.current(), NEW)

    def test_recovery_before_switch(self):
        self.prepare_switch()
        self.move("ROLLING_BACK")
        self.switch()

        self.assertEqual(self.current(), OLD)

    def test_unexpected_current_release_is_rejected(self):
        self.prepare_switch()

        (self.root / "current").unlink()
        (self.root / "current").symlink_to(
            "releases/" + OTHER
        )

        with self.assertRaises(ValueError):
            self.switch()

        self.assertEqual(self.current(), OTHER)

    def test_rollback_failure_blocks_new_deployment(self):
        self.prepare_switch()
        self.switch()
        self.move("ROLLING_BACK")
        self.move("ROLLBACK_FAILED")

        with self.assertRaises(ValueError):
            state.begin(self.directory, NEW, OTHER)

    def test_terminal_state_allows_new_deployment(self):
        self.begin()
        self.move("FAILED_BEFORE_SWITCH")

        result = state.begin(
            self.directory, OLD, OTHER
        )

        self.assertEqual(result["target"], OTHER)

    def test_corrupted_state_fails_closed(self):
        (self.directory / "state.json").write_text(
            "invalid json"
        )

        with self.assertRaises(ValueError):
            state.begin(self.directory, OLD, NEW)

    def test_concurrent_deployment_is_rejected(self):
        path = self.directory / "deploy.lock"

        with path.open("w") as lock:
            fcntl.flock(
                lock.fileno(), fcntl.LOCK_EX
            )

            result = subprocess.run(
                [
                    sys.executable,
                    state.__file__,
                    "lock",
                    str(self.directory),
                    "/bin/echo",
                    "unexpected execution",
                ],
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "Another deployment",
            result.stderr,
        )

    def test_lock_allows_exclusive_execution(self):
        result = subprocess.run(
            [
                sys.executable,
                state.__file__,
                "lock",
                str(self.directory),
                "/bin/echo",
                "locked execution",
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn(
            "locked execution",
            result.stdout,
        )


if __name__ == "__main__":
    unittest.main()
