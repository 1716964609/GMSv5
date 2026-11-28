import os as _os, sys as _sys  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import no_network  # noqa: E402,F401  Blocks real network access (no AWS calls).
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from botocore.exceptions import ClientError

path = Path(__file__).resolve().parents[1] / "lambda" / "handler.py"
spec = importlib.util.spec_from_file_location("http_handler_test", path)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

EVENT = {"time": "2026-11-16T19:10:00Z"}


def duplicate_error():
    return ClientError(
        {"Error": {
            "Code": "ConditionalCheckFailedException",
            "Message": "Duplicate minute",
        }},
        "UpdateItem",
    )


class HTTPAlertTests(unittest.TestCase):

    def test_first_failure_no_notification(self):
        db = MagicMock()
        db.update_item.return_value = {
            "Attributes": {"http_failures": 1}
        }

        with patch.object(h, "table", return_value=db), \
             patch.object(h, "notify") as notify:
            result = h.track_http_status(EVENT, False)

        self.assertEqual(result["failures"], 1)
        notify.assert_not_called()

    def test_second_failure_sends_down(self):
        db = MagicMock()
        db.update_item.side_effect = [
            {"Attributes": {
                "http_failures": 2,
                "first_fail_at": "2026-11-16T19:09:00Z",
                "http_status": "up",
            }},
            {},
        ]

        with patch.object(h, "table", return_value=db), \
             patch.object(h, "notify") as notify:
            result = h.track_http_status(EVENT, False)

        self.assertEqual(result["transition"], "down")
        self.assertEqual(db.update_item.call_count, 2)
        notify.assert_called_once()
        self.assertIn("HTTP DOWN", notify.call_args.args[0])

    def test_recovery_sends_one_notification(self):
        db = MagicMock()
        db.update_item.return_value = {
            "Attributes": {
                "http_status": "down",
                "down_since": "2026-11-16T19:09:00Z",
            }
        }

        with patch.object(h, "table", return_value=db), \
             patch.object(h, "notify") as notify:
            result = h.track_http_status(EVENT, True)

        self.assertEqual(result["transition"], "recovered")
        notify.assert_called_once()
        self.assertIn("RECOVERED", notify.call_args.args[0])

    def test_duplicate_minute_no_notification(self):
        db = MagicMock()
        db.update_item.side_effect = duplicate_error()

        with patch.object(h, "table", return_value=db), \
             patch.object(h, "notify") as notify:
            result = h.track_http_status(EVENT, False)

        self.assertEqual(result, {"duplicate": True})
        notify.assert_not_called()


    def test_failure_update_has_no_unused_aliases(self):
        db = MagicMock()
        db.update_item.return_value = {
            "Attributes": {"http_failures": 1}
        }

        with patch.object(h, "table", return_value=db):
            h.track_http_status(EVENT, False)

        kwargs = db.update_item.call_args.kwargs
        expression = (
            kwargs["UpdateExpression"]
            + " "
            + kwargs["ConditionExpression"]
        )

        for alias in kwargs["ExpressionAttributeNames"]:
            self.assertIn(alias, expression)


if __name__ == "__main__":
    unittest.main()
