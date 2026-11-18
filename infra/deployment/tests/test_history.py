import json
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
NEXT = "c" * 40


class HistoryTests(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        self.directory = Path(temp.name)

    def finish_transaction(self):
        state.begin(self.directory, OLD, NEW)

        return state.transition(
            self.directory,
            "FAILED_BEFORE_SWITCH",
            "test_failure",
        )

    def test_new_transaction_has_unique_id(self):
        first = self.finish_transaction()

        second = state.begin(
            self.directory, OLD, NEXT
        )

        self.assertNotEqual(
            first["transaction_id"],
            second["transaction_id"],
        )

    def test_completed_transaction_is_preserved(self):
        first = self.finish_transaction()

        state.begin(self.directory, OLD, NEXT)

        path = (
            self.directory / "history" /
            (first["transaction_id"] + ".json")
        )

        self.assertEqual(
            json.loads(path.read_text()),
            first,
        )

    def test_archive_is_idempotent(self):
        first = self.finish_transaction()

        state.archive_terminal_state(
            self.directory, first
        )

        state.archive_terminal_state(
            self.directory, first
        )

        self.assertEqual(
            len(list(
                (self.directory / "history").glob("*.json")
            )),
            1,
        )

    def test_active_transaction_cannot_be_archived(self):
        active = state.begin(
            self.directory, OLD, NEW
        )

        with self.assertRaises(ValueError):
            state.archive_terminal_state(
                self.directory, active
            )

    def test_history_conflict_blocks_new_transaction(self):
        first = self.finish_transaction()

        state.archive_terminal_state(
            self.directory, first
        )

        path = (
            self.directory / "history" /
            (first["transaction_id"] + ".json")
        )

        path.write_text("{}")

        with self.assertRaises(ValueError):
            state.begin(self.directory, OLD, NEXT)

        self.assertEqual(
            state.read_state(self.directory),
            first,
        )


if __name__ == "__main__":
    unittest.main()
