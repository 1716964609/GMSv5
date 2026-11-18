import sys
import tempfile
import unittest

from pathlib import Path
from unittest.mock import Mock

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)

import boot_recovery
import state

OLD = "a" * 40
NEW = "b" * 40
OTHER = "c" * 40


class BootRecoveryTests(unittest.TestCase):

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

        self.current = self.root / "current"
        self.current.symlink_to("releases/" + OLD)

    def phase(self):
        return state.read_state(
            self.directory
        )["phase"]

    def active(self):
        return state.current_release(self.root)

    def prepare(self):
        boot_recovery.prepare(
            self.root, self.directory
        )

    def verify(self, health):
        return boot_recovery.verify(
            self.root, self.directory, health
        )

    def interrupt_after_switch(self):
        state.begin(self.directory, OLD, NEW)
        state.transition(self.directory, "VERIFIED")
        state.transition(self.directory, "SWITCHING")
        state.switch_release(self.directory, self.root)
        state.transition(self.directory, "VALIDATING")

    def test_boot_without_transaction(self):
        self.prepare()

        self.assertEqual(self.active(), OLD)

        health = Mock()

        self.assertTrue(self.verify(health))
        health.assert_not_called()

    def test_interrupted_preparing(self):
        state.begin(self.directory, OLD, NEW)

        self.prepare()

        self.assertEqual(self.active(), OLD)
        self.assertEqual(
            self.phase(), "FAILED_BEFORE_SWITCH"
        )

    def test_interrupted_verified(self):
        state.begin(self.directory, OLD, NEW)
        state.transition(self.directory, "VERIFIED")

        self.prepare()

        self.assertEqual(self.active(), OLD)
        self.assertEqual(
            self.phase(), "FAILED_BEFORE_SWITCH"
        )

    def test_interrupted_after_switch(self):
        self.interrupt_after_switch()

        self.prepare()

        self.assertEqual(self.active(), OLD)
        self.assertEqual(
            self.phase(), "ROLLING_BACK"
        )

    def test_boot_recovery_success(self):
        self.interrupt_after_switch()
        self.prepare()

        health = Mock(return_value=True)

        self.assertTrue(self.verify(health))

        health.assert_called_once_with(OLD)

        self.assertEqual(
            self.phase(), "ROLLED_BACK"
        )

    def test_boot_recovery_health_failure(self):
        self.interrupt_after_switch()
        self.prepare()

        self.assertFalse(
            self.verify(Mock(return_value=False))
        )

        self.assertEqual(
            self.phase(), "ROLLBACK_FAILED"
        )

        self.assertEqual(self.active(), OLD)

    def test_successful_deployment_survives_reboot(self):
        self.interrupt_after_switch()
        state.transition(self.directory, "SUCCEEDED")

        self.prepare()

        self.assertEqual(self.active(), NEW)

        health = Mock()
        self.assertTrue(self.verify(health))
        health.assert_not_called()

    def test_terminal_state_mismatch_blocks_boot(self):
        self.interrupt_after_switch()
        state.transition(self.directory, "SUCCEEDED")

        self.current.unlink()
        self.current.symlink_to("releases/" + OTHER)

        with self.assertRaises(ValueError):
            self.prepare()

    def test_repeated_boot_recovery_is_idempotent(self):
        self.interrupt_after_switch()

        self.prepare()
        self.prepare()

        self.assertEqual(self.active(), OLD)

        self.assertTrue(
            self.verify(Mock(return_value=True))
        )

        self.prepare()

        self.assertEqual(
            self.phase(), "ROLLED_BACK"
        )

    def test_retry_failed_rollback_after_reboot(self):
        self.interrupt_after_switch()
        self.prepare()

        self.assertFalse(
            self.verify(Mock(return_value=False))
        )

        self.prepare()

        self.assertTrue(
            self.verify(Mock(return_value=True))
        )

        self.assertEqual(
            self.phase(), "ROLLED_BACK"
        )

    def test_missing_previous_release_blocks_recovery(self):
        self.interrupt_after_switch()

        (
            self.root / "releases" /
            OLD / "server.js"
        ).unlink()

        with self.assertRaises(ValueError):
            self.prepare()

        self.assertEqual(
            self.phase(), "ROLLBACK_FAILED"
        )


if __name__ == "__main__":
    unittest.main()
