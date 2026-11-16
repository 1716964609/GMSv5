import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

from botocore.exceptions import ClientError

path = Path(__file__).resolve().parents[1] / "lambda" / "handler.py"
spec = importlib.util.spec_from_file_location("availability_handler_test", path)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


class MemoryTable:
    def __init__(self):
        self.items = {}

    def put_item(self, *, Item, ConditionExpression):
        key = (Item["instance_id"], Item["event_time"])
        if key in self.items:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ConditionalCheckFailedException",
                        "Message": "Duplicate",
                    }
                },
                "PutItem",
            )
        self.items[key] = Item


class IdempotencyIntegrationTests(unittest.TestCase):
    def test_synthetic_same_id_only_writes_and_notifies_once(self):
        db = MemoryTable()
        event = {
            "source": "gmsv5.manual-test",
            "test_mode": True,
            "id": "regression-test-001",
        }

        with (
            patch.object(h, "configuration", return_value={}),
            patch.object(h, "table", return_value=db),
            patch.object(h, "metric") as metric,
            patch.object(h, "notify") as notify,
        ):
            first = h.handler(event)
            second = h.handler(event)

        self.assertEqual(first, {"synthetic": True, "new": True})
        self.assertEqual(second, {"synthetic": True, "new": False})
        self.assertEqual(len(db.items), 1)
        self.assertIn(
            ("test-availability", "synthetic#regression-test-001"),
            db.items,
        )
        metric.assert_called_once()
        notify.assert_called_once()

    def test_real_event_deduplication_is_unchanged(self):
        db = MemoryTable()
        event = {
            "id": "ec2-event-001",
            "time": "2026-11-16T09:10:20Z",
        }

        with patch.object(h, "table", return_value=db):
            first = h.save_event("i-test", event, "stopped")
            second = h.save_event("i-test", event, "stopped")

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(len(db.items), 1)


if __name__ == "__main__":
    unittest.main()
