"""Local tests: no AWS calls, no public HTTP requests, no production actions."""
import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gms_handler", ROOT / "lambda" / "handler.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

ENV = {
    "TABLE_NAME": "test-only",
    "APP_INSTANCE_ID": "i-app",
    "OBS_INSTANCE_ID": "i-obs",
    "DISCORD_CLOUDFRONT_URL": "https://example.cloudfront.net/notify",
    "DISCORD_TOKEN_PARAMETER": "/test/relay-token",
}


def ec2_event(event_type, state, iid="i-app", at="2026-11-16T06:00:00Z"):
    return {
        "source": "aws.ec2", "detail-type": event_type, "id": "evt-123",
        "time": at, "detail": {"instance-id": iid, "state": state},
    }


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_utc_timestamp(self):
        self.assertEqual(h.timestamp("2026-11-16T15:06:35+09:00"),
                         "2026-11-16T06:06:35Z")

    def test_downtime_nonnegative(self):
        self.assertEqual(h.seconds_between("2026-11-16T06:00:00Z",
                                          "2026-11-16T06:01:39Z"), 99)
        self.assertEqual(h.seconds_between("2026-11-16T06:02:00Z",
                                          "2026-11-16T06:01:39Z"), 0)

    def test_spot_trigger_only_after_warning(self):
        self.assertEqual(h.spot_trigger({"last_spot_warning": "2026-11-16T06:00:00Z"},
                                         "2026-11-16T06:01:00Z"), "spot_warning_observed")
        self.assertEqual(h.spot_trigger({}, "2026-11-16T06:01:00Z"), "unknown")

    def test_no_forged_spot_attribution(self):
        self.assertEqual(h.spot_trigger({"last_spot_warning": "2026-11-16T05:00:00Z"},
                                         "2026-11-16T06:01:00Z"), "unknown")

    def test_ignores_foreign_instance(self):
        evt = ec2_event(h.STATE_EVENT, "stopped", iid="i-foreign")
        self.assertEqual(h.handle_ec2(evt), {"ignored": "unmonitored instance"})

    def test_ignores_unrequested_state(self):
        evt = ec2_event(h.STATE_EVENT, "stopping")
        self.assertEqual(h.handle_ec2(evt), {"ignored": "other state"})

    @patch.object(h, "save_state")
    @patch.object(h, "save_event", return_value=True)
    @patch.object(h, "read_state", return_value={})
    def test_stopped_record(self, get, put, state):
        result = h.handle_ec2(ec2_event(h.STATE_EVENT, "stopped"))
        self.assertEqual(result, {"stored": "stopped"})
        put.assert_called_once()
        self.assertEqual(state.call_args.args[0], "i-app")
        self.assertEqual(state.call_args.args[1]["ec2_state"], "stopped")

    @patch.object(h, "save_event", return_value=False)
    @patch.object(h, "read_state", return_value={})
    def test_duplicate_does_not_notify(self, get, put):
        result = h.handle_ec2(ec2_event(h.STATE_EVENT, "stopped"))
        self.assertEqual(result, {"duplicate": True})

    @patch.object(h, "notify")
    @patch.object(h, "save_state")
    @patch.object(h, "save_event", return_value=True)
    def test_spot_warning_notifies(self, put, state, notify):
        evt = ec2_event(h.WARNING_EVENT, "", iid="i-obs")
        self.assertTrue(h.handle_ec2(evt)["new"])
        notify.assert_called_once()

    @patch.object(h, "notify")
    @patch.object(h, "metric")
    @patch.object(h, "save_event", return_value=True)
    def test_synthetic_does_not_touch_real_instance(self, save, metric, notify):
        result = h.handler({"source": "gmsv5.manual-test", "test_mode": True, "id": "smoke"})
        self.assertEqual(result, {"synthetic": True, "new": True})
        self.assertEqual(save.call_args.args[0], "test-availability")
        metric.assert_called_once()
        notify.assert_called_once()

    @patch.object(h, "track_http_status")
    @patch.object(h, "metric")
    @patch.object(h, "read_state", return_value={})
    @patch.object(h, "probe_http", return_value=False)
    def test_scheduled_http_down(self, probe, get, metric, track):
        result = h.handler({"source": "aws.events", "detail-type": "Scheduled Event"})
        self.assertEqual(result, {"http_up": False})
        self.assertEqual(metric.call_args.args[:2], ("HTTPUp", 0))

    @patch.object(h, "track_http_status")
    @patch.object(h, "metric")
    @patch.object(h, "complete_incident")
    @patch.object(h, "read_state", return_value={"ec2_state": "running"})
    @patch.object(h, "probe_http", return_value=True)
    def test_scheduled_http_recovers_pending(self, probe, get, complete, metric, track):
        result = h.handler({"source": "aws.events", "detail-type": "Scheduled Event"})
        self.assertEqual(result, {"http_up": True})
        complete.assert_called_once()
        self.assertEqual(complete.call_args.kwargs["proof"], "http200")


if __name__ == "__main__":
    unittest.main()
