"""Handler-level Spot evidence tests: existing monitoring must stay intact.

No AWS calls: DynamoDB and EC2 are fakes; boto3 client/resource creation fails.
"""
import os as _os, sys as _sys  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import no_network  # noqa: E402,F401  Blocks real network access (no AWS calls).
import importlib.util
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambda"))

import spot_status  # noqa: E402
from fake_dynamodb import FakeTable  # noqa: E402
from test_spot_status import FakeEC2, throttle, utc  # noqa: E402

REAL_TABLE = spot_status.table  # before any test patches it

spec = importlib.util.spec_from_file_location(
    "spot_handler_test", Path(__file__).resolve().parents[1] / "lambda" / "handler.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

ENV = {
    "TABLE_NAME": "test-only",
    "APP_INSTANCE_ID": "i-app",
    "OBS_INSTANCE_ID": "i-obs",
    "DISCORD_CLOUDFRONT_URL": "https://example.cloudfront.net/notify",
    "DISCORD_TOKEN_PARAMETER": "/test/relay-token",
    "SPOT_STATUS_COLLECTION": "enabled",
}


def state_event(state, at, iid="i-app", event_id=None):
    return {"source": "aws.ec2", "detail-type": h.STATE_EVENT,
            "id": event_id or f"evt-{state}-{at}", "time": at,
            "detail": {"instance-id": iid, "state": state}}


def signal_event(detail_type, at, iid="i-app"):
    return {"source": "aws.ec2", "detail-type": detail_type, "id": f"evt-signal-{at}",
            "time": at, "detail": {"instance-id": iid, "instance-action": "stop"}}


def s_budget_floor():
    return spot_status.ATTEMPT_WORST_MS + spot_status.SAFETY_MS - 1


class Context:
    def __init__(self, remaining=85000):
        self.remaining = remaining

    def get_remaining_time_in_millis(self):
        return self.remaining


class SpotHandlerTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeTable()
        self.ec2 = FakeEC2([("instance-stopped-no-capacity", "2026-11-20T09:59:58Z")])
        self.notices = []
        self.metrics = []
        self.clock = "2026-11-20T10:00:05Z"
        patches = [
            patch.dict(os.environ, ENV),
            patch("boto3.client", side_effect=AssertionError("real AWS client")),
            patch("boto3.resource", side_effect=AssertionError("real AWS resource")),
            patch.object(h, "table", return_value=self.db),
            patch.object(h, "notify", side_effect=self.notices.append),
            patch.object(h, "metric", side_effect=lambda *a, **k: self.metrics.append((a, k))),
            patch.object(h, "probe_http", return_value=True),
            patch.object(h, "now_utc", side_effect=lambda: utc(self.clock)),
            patch.object(spot_status, "ec2", side_effect=lambda: self.ec2),
            patch.object(spot_status, "table", return_value=self.db),
            patch.object(spot_status, "now_utc", side_effect=lambda: utc(self.clock)),
            patch.object(spot_status.time, "sleep"),
            patch.object(h.time, "sleep"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def incidents(self, iid="i-app"):
        return [row for row in self.db.partition(iid) if row.get("event_type") == "stopped"]

    def state(self, iid="i-app"):
        return self.db.row(iid, h.STATE_KEY) or {}

    def stop_and_recover(self, stop="2026-11-20T10:00:00Z", running="2026-11-20T10:02:00Z"):
        self.clock = stop
        stopped = h.handle_ec2(state_event("stopped", stop), Context())
        self.clock = running
        resumed = h.handle_ec2(state_event("running", running), Context())
        return stopped, resumed

    # --- End-to-end -----------------------------------------------------

    def test_capacity_reason_reaches_incident_and_recovered_notification(self):
        self.ec2.launch = "2026-11-20T08:00:00Z"
        stopped, resumed = self.stop_and_recover()
        self.assertEqual(stopped, {"stored": "stopped"})
        self.assertEqual(resumed, {"stored": "running", "incident": True})
        incident = self.incidents()[0]
        self.assertEqual(incident["spot_reason"], "CAPACITY")
        self.assertTrue(incident["spot_reason_confirmed"])
        self.assertEqual(incident["spot_status_code"], "instance-stopped-no-capacity")
        self.assertEqual(incident["recovery_proof"], "http200")
        self.assertEqual(incident["downtime_seconds"], 120)  # stop_at -> HTTP 200
        recovered = [n for n in self.notices if n.startswith("[GMS v5 RECOVERED]")]
        self.assertEqual(len(recovered), 1)
        self.assertIn("i-app: 120s (event-time based); HTTP 200 を実測", recovered[0])
        self.assertIn("Spot cause: CAPACITY (confirmed by AWS Spot request status)",
                      recovered[0])
        self.assertIn("AWS code: instance-stopped-no-capacity", recovered[0])
        self.assertIn("observed: 2026-11-20T10:00:00.000Z", recovered[0])
        stop_metric = [m for m in self.metrics if m[0][0] == "StopToRecoverySeconds"]
        self.assertEqual(stop_metric[0][0][1], 120)

    def test_running_collection_runs_after_recovery_processing(self):
        recovered_before_call = []
        self.ec2.before_spot_call = lambda: recovered_before_call.append(
            sum(n.startswith("[GMS v5 RECOVERED]") for n in self.notices))
        self.stop_and_recover()
        # stopped-phase call before recovery; running-phase call after it.
        self.assertEqual(recovered_before_call, [0, 1])

    def test_fulfilled_after_resume_keeps_confirmed_reason(self):
        self.ec2.statuses = [("instance-stopped-no-capacity", "2026-11-20T09:59:58Z"),
                             ("fulfilled", "2026-11-20T10:02:00Z")]
        self.stop_and_recover()
        incident = self.incidents()[0]
        self.assertEqual(incident["spot_reason"], "CAPACITY")
        running_rows = [r for r in self.db.partition("spot#i-app") if r["phase"] == "running"]
        self.assertEqual(running_rows[0]["status_code"], "fulfilled")
        # Set at the restart, so it belongs to the next run and is not offered to
        # this incident. EvidenceWriteTests cover the conditional write itself.
        self.assertEqual(running_rows[0]["attribution"], "after_incident")

    def test_observability_running_only_proof_is_preserved(self):
        self.ec2 = FakeEC2([("instance-stopped-no-capacity", "2026-11-20T09:59:58Z")])
        self.clock = "2026-11-20T10:00:00Z"
        h.handle_ec2(state_event("stopped", "2026-11-20T10:00:00Z", iid="i-obs"), Context())
        self.clock = "2026-11-20T10:01:00Z"
        h.handle_ec2(state_event("running", "2026-11-20T10:01:00Z", iid="i-obs"), Context())
        incident = self.incidents("i-obs")[0]
        self.assertEqual(incident["recovery_proof"], "ec2_running_only")
        self.assertIn("EC2 running（サービス復旧は未確認）", self.notices[-1])

    # --- Fail open -------------------------------------------------------

    def test_api_failures_do_not_block_incident_or_recovery(self):
        self.ec2 = FakeEC2([throttle()])
        stopped, resumed = self.stop_and_recover()
        self.assertEqual(stopped, {"stored": "stopped"})
        self.assertEqual(resumed, {"stored": "running", "incident": True})
        incident = self.incidents()[0]
        self.assertEqual(incident["spot_reason"], "UNKNOWN")
        self.assertFalse(incident["spot_reason_confirmed"])
        self.assertEqual(incident["downtime_seconds"], 120)
        self.assertIn("Spot cause: UNKNOWN", self.notices[-1])

    def test_unexpected_collector_crash_does_not_break_monitoring(self):
        with patch.object(spot_status, "on_stopped", side_effect=RuntimeError("bug")), \
             patch.object(spot_status, "on_running", side_effect=RuntimeError("bug")), \
             patch.object(spot_status, "incident_summary", side_effect=RuntimeError("bug")):
            stopped, resumed = self.stop_and_recover()
        self.assertEqual(stopped, {"stored": "stopped"})
        self.assertEqual(resumed, {"stored": "running", "incident": True})
        self.assertEqual(self.state()["active_stop"], "")
        self.assertIn("Spot cause: UNKNOWN (summary unavailable)", self.notices[-1])

    def test_ec2_client_creation_failure_fails_open(self):
        with patch.object(spot_status, "ec2", side_effect=RuntimeError("no region")):
            stopped, _ = self.stop_and_recover()
        self.assertEqual(stopped, {"stored": "stopped"})
        self.assertEqual(len(self.incidents()), 1)

    def test_missing_spot_request_stays_unknown(self):
        self.ec2.request_id = None
        self.stop_and_recover()
        self.assertEqual(self.incidents()[0]["spot_reason"], "UNKNOWN")
        errors = {row.get("error") for row in self.db.partition("spot#i-app")}
        self.assertEqual(errors, {"missing_spot_request_id"})

    # --- Signals ------------------------------------------------------------

    def test_warning_is_sent_unchanged_before_status_collection(self):
        self.ec2 = FakeEC2([("marked-for-stop", "2026-11-20T09:58:00Z")], state="running")
        self.clock = "2026-11-20T09:58:00Z"
        notices_before_ec2 = []
        original = self.ec2.describe_instances
        self.ec2.describe_instances = lambda **kw: (notices_before_ec2.append(
            len(self.notices)), original(**kw))[1]
        result = h.handle_ec2(signal_event(h.WARNING_EVENT, "2026-11-20T09:58:00Z"),
                              Context())
        self.assertTrue(result["new"])
        self.assertEqual(self.notices,
                         ["[GMS v5 INTERRUPTION WARNING] i-app at 2026-11-20T09:58:00Z; stop"])
        self.assertEqual(notices_before_ec2, [1])  # notification first, then the read
        row = self.db.partition("spot#i-app")[0]
        self.assertEqual((row["phase"], row["status_code"], row["classification"]),
                         ("warning", "marked-for-stop", "PENDING"))
        state = self.state()
        self.assertEqual(state["last_spot_warning"], "2026-11-20T09:58:00Z")
        self.assertEqual(state["last_interruption_warning"], "2026-11-20T09:58:00Z")
        self.assertNotIn("last_rebalance_recommendation", state)

    def test_rebalance_is_kept_separate_from_warning(self):
        self.ec2 = FakeEC2([("fulfilled", "2026-11-20T08:00:00Z")], state="running")
        h.handle_ec2(signal_event(h.REBALANCE_EVENT, "2026-11-20T09:50:00Z"), Context())
        state = self.state()
        self.assertEqual(state["last_rebalance_recommendation"], "2026-11-20T09:50:00Z")
        self.assertNotIn("last_interruption_warning", state)
        self.assertEqual(state["last_spot_warning"], "2026-11-20T09:50:00Z")  # legacy
        self.assertEqual(self.notices,
                         ["[GMS v5 REBALANCE RECOMMENDATION] i-app at 2026-11-20T09:50:00Z; stop"])

    def test_collector_failure_or_hang_cannot_block_warning(self):
        with patch.object(spot_status, "on_signal", side_effect=RuntimeError("bug")):
            result = h.handle_ec2(signal_event(h.WARNING_EVENT, "2026-11-20T09:58:00Z"),
                                  Context())
        self.assertTrue(result["new"])
        self.assertEqual(len(self.notices), 1)

    def test_discord_failure_still_stores_evidence_and_raises(self):
        with patch.object(h, "notify", side_effect=RuntimeError("relay down")):
            with self.assertRaises(RuntimeError):
                h.handle_ec2(signal_event(h.WARNING_EVENT, "2026-11-20T09:58:00Z"),
                             Context())
        self.assertEqual(len(self.db.partition("spot#i-app")), 1)

    def test_duplicate_warning_notifies_once(self):
        event = signal_event(h.WARNING_EVENT, "2026-11-20T09:58:00Z")
        h.handle_ec2(event, Context())
        h.handle_ec2(event, Context())
        self.assertEqual(len(self.notices), 1)

    def test_warning_and_rebalance_attach_to_incident(self):
        self.ec2.launch = "2026-11-20T08:00:00Z"
        h.handle_ec2(signal_event(h.REBALANCE_EVENT, "2026-11-20T09:55:00Z"), Context())
        h.handle_ec2(signal_event(h.WARNING_EVENT, "2026-11-20T09:58:00Z"), Context())
        self.stop_and_recover()
        incident = self.incidents()[0]
        self.assertEqual(incident["rebalance_recommendation_at"], "2026-11-20T09:55:00Z")
        self.assertEqual(incident["interruption_warning_at"], "2026-11-20T09:58:00Z")
        self.assertEqual(incident["trigger"], "spot_warning_observed")  # legacy rule

    def test_warning_alone_never_confirms_capacity(self):
        self.ec2 = FakeEC2([throttle()])
        h.handle_ec2(signal_event(h.WARNING_EVENT, "2026-11-20T09:58:00Z"), Context())
        self.stop_and_recover()
        incident = self.incidents()[0]
        self.assertEqual(incident["spot_reason"], "UNKNOWN")
        self.assertEqual(incident["interruption_warning_at"], "2026-11-20T09:58:00Z")

    # --- stopping ------------------------------------------------------------

    def test_stopping_records_time_without_opening_incident(self):
        self.ec2 = FakeEC2([("marked-for-stop", "2026-11-20T09:58:00Z")], state="stopping")
        result = h.handle_ec2(state_event("stopping", "2026-11-20T09:59:30Z"), Context())
        self.assertEqual(result, {"stored": "stopping"})
        self.assertEqual(self.incidents(), [])
        self.assertEqual(self.state()["last_stopping"], "2026-11-20T09:59:30Z")
        self.assertNotIn("ec2_state", self.state())
        self.assertEqual(self.notices, [])

    def test_stopping_then_stopped_sets_stopping_at_but_not_stop_at(self):
        self.ec2 = FakeEC2([("instance-stopped-no-capacity", "2026-11-20T09:59:35Z")],
                           state="stopping")
        h.handle_ec2(state_event("stopping", "2026-11-20T09:59:30Z"), Context())
        self.ec2.state = "stopped"
        self.stop_and_recover()
        incident = self.incidents()[0]
        self.assertEqual(incident["stopping_at"], "2026-11-20T09:59:30Z")
        self.assertEqual(incident["stop_at"], "2026-11-20T10:00:00Z")
        self.assertEqual(incident["downtime_seconds"], 120)
        # The stopping-phase observation is linked to the incident when stopped arrives.
        stopping_row = [r for r in self.db.partition("spot#i-app")
                        if r["phase"] == "stopping"][0]
        self.assertEqual(stopping_row["incident_key"], incident["event_time"])
        self.assertEqual(stopping_row["attribution"], "in_window")
        self.assertEqual(incident["spot_reason"], "CAPACITY")

    def test_late_stopping_after_stopped_is_attached(self):
        self.clock = "2026-11-20T10:00:00Z"
        h.handle_ec2(state_event("stopped", "2026-11-20T10:00:00Z"), Context())
        h.handle_ec2(state_event("stopping", "2026-11-20T09:59:30Z"), Context())
        self.assertEqual(self.incidents()[0]["stopping_at"], "2026-11-20T09:59:30Z")

    def test_disabled_flag_keeps_legacy_behaviour(self):
        with patch.dict(os.environ, {"SPOT_STATUS_COLLECTION": "disabled"}):
            ignored = h.handle_ec2(state_event("stopping", "2026-11-20T09:59:30Z"))
            self.stop_and_recover()
        self.assertEqual(ignored, {"ignored": "other state"})
        self.assertEqual(self.ec2.calls, [])
        self.assertEqual(self.db.partition("spot#i-app"), [])
        self.assertEqual(self.notices[-1],
                         "[GMS v5 RECOVERED] i-app: 120s (event-time based); HTTP 200 を実測")

    # --- Ordering, duplicates and rapid cycles ---------------------------------

    def test_duplicate_stopped_event_adds_nothing(self):
        event = state_event("stopped", "2026-11-20T10:00:00Z", event_id="evt-dup")
        self.clock = "2026-11-20T10:00:00Z"
        h.handle_ec2(event, Context())
        before = len(self.db.partition("spot#i-app"))
        self.assertEqual(h.handle_ec2(event, Context()), {"duplicate": True})
        self.assertEqual(len(self.db.partition("spot#i-app")), before)
        self.assertEqual(len(self.ec2.calls), 2)  # one observation only

    def test_rapid_cycle_does_not_reuse_previous_reason(self):
        self.ec2.launch = "2026-11-20T08:00:00Z"
        self.stop_and_recover("2026-11-20T10:00:00Z", "2026-11-20T10:02:00Z")
        # The instance restarted at 10:02. AWS still shows the old status.
        self.ec2.launch = "2026-11-20T10:02:00Z"
        self.ec2.statuses = [("instance-stopped-no-capacity", "2026-11-20T09:59:58Z")]
        self.stop_and_recover("2026-11-20T10:05:00Z", "2026-11-20T10:07:00Z")
        first, second = self.incidents()
        self.assertEqual(first["spot_reason"], "CAPACITY")
        self.assertEqual(second["spot_window_start"], "2026-11-20T10:02:00Z")
        self.assertEqual(second["spot_reason"], "UNKNOWN")
        self.assertIn("Spot cause: UNKNOWN", self.notices[-1])
        attributions = {r["attribution"] for r in self.db.partition("spot#i-app")
                        if r["source_event_time"] == "2026-11-20T10:05:00Z"}
        self.assertEqual(attributions, {"stale"})

    def test_late_stopped_after_running_keeps_reason_on_correct_incident(self):
        self.ec2.launch = "2026-11-20T10:02:00Z"
        self.ec2.state = "running"
        self.clock = "2026-11-20T10:02:00Z"
        h.handle_ec2(state_event("running", "2026-11-20T10:02:00Z"), Context())
        self.clock = "2026-11-20T10:02:30Z"
        result = h.handle_ec2(state_event("stopped", "2026-11-20T10:00:00Z"), Context())
        self.assertEqual(result, {"stored": "late_stopped_paired"})
        incident = self.incidents()[0]
        self.assertEqual(incident["spot_reason"], "CAPACITY")
        self.assertEqual(incident["running_at"], "2026-11-20T10:02:00Z")

    def test_interruption_right_after_restart_stays_with_its_own_incident(self):
        # A: stopped 10:00 (capacity). Restart 10:02:00. B: stop status set
        # 10:02:20, inside the former 60 s grace, before A's running read.
        self.ec2.launch = "2026-11-20T08:00:00Z"
        self.clock = "2026-11-20T10:00:00Z"
        h.handle_ec2(state_event("stopped", "2026-11-20T10:00:00Z"), Context())
        self.ec2.launch, self.ec2.state = "2026-11-20T10:02:00Z", "stopping"
        self.ec2.statuses = [("instance-stopped-by-user", "2026-11-20T10:02:20Z")]
        self.clock = "2026-11-20T10:02:30Z"
        h.handle_ec2(state_event("running", "2026-11-20T10:02:00Z"), Context())
        self.ec2.state = "stopped"
        self.clock = "2026-11-20T10:03:00Z"
        h.handle_ec2(state_event("stopped", "2026-11-20T10:03:00Z"), Context())
        first, second = self.incidents()
        self.assertEqual(first["spot_reason"], "CAPACITY")
        self.assertNotIn("spot_reason_conflict", first)
        self.assertEqual(second["spot_reason"], "USER")
        running_row = [r for r in self.db.partition("spot#i-app") if r["phase"] == "running"][0]
        self.assertEqual(running_row["attribution"], "after_incident")

    def test_missing_then_delayed_running_event_does_not_mix_reasons(self):
        self.ec2.launch = "2026-11-20T08:00:00Z"
        self.clock = "2026-11-20T10:00:00Z"
        h.handle_ec2(state_event("stopped", "2026-11-20T10:00:00Z"), Context())
        # A's running event (10:02) is not delivered yet. B stops at 10:05.
        self.ec2.launch = "2026-11-20T10:02:00Z"
        self.ec2.statuses = [("instance-stopped-by-user", "2026-11-20T10:04:58Z")]
        self.clock = "2026-11-20T10:05:00Z"
        h.handle_ec2(state_event("stopped", "2026-11-20T10:05:00Z"), Context())
        first, second = self.incidents()
        self.assertEqual((first["spot_reason"], second["spot_reason"]), ("CAPACITY", "USER"))
        self.assertEqual(second["spot_run_started_at"], "2026-11-20T10:02:00Z")
        # A's running event finally arrives; the instance is stopped (B) now.
        self.clock = "2026-11-20T10:06:00Z"
        h.handle_ec2(state_event("running", "2026-11-20T10:02:00Z"), Context())
        first, second = self.incidents()
        self.assertEqual(first["running_at"], "2026-11-20T10:02:00Z")
        self.assertEqual(first["spot_reason"], "CAPACITY")
        self.assertNotIn("spot_reason_conflict", first)
        self.assertEqual(second["spot_reason"], "USER")

    def test_late_stopping_of_previous_cycle_is_not_attached_to_next(self):
        self.clock = "2026-11-20T10:00:00Z"
        h.handle_ec2(state_event("stopped", "2026-11-20T10:00:00Z"), Context())
        # A's running event is lost, so B's window floor is only 10 minutes.
        self.clock = "2026-11-20T10:06:00Z"
        h.handle_ec2(state_event("stopped", "2026-11-20T10:06:00Z"), Context())
        h.handle_ec2(state_event("stopping", "2026-11-20T09:59:30Z"), Context())
        first, second = self.incidents()
        self.assertEqual(second["spot_window_start"], "2026-11-20T09:56:00Z")
        self.assertNotIn("stopping_at", second)

    def test_lambda_time_budget_is_respected_on_stopped(self):
        context = Context(remaining=s_budget_floor())
        self.ec2.statuses = [("marked-for-stop", "2026-11-20T09:58:00Z")]
        self.clock = "2026-11-20T10:00:00Z"
        self.assertEqual(h.handle_ec2(state_event("stopped", "2026-11-20T10:00:00Z"),
                                      context), {"stored": "stopped"})
        self.assertEqual(self.ec2.calls, [])  # not enough time for one safe attempt
        self.assertEqual(len(self.incidents()), 1)

    def test_collector_uses_its_own_bounded_table_client(self):
        handler_db = FakeTable()
        with patch.object(h, "table", return_value=handler_db):
            self.clock = "2026-11-20T10:00:00Z"
            h.handle_ec2(state_event("stopped", "2026-11-20T10:00:00Z"), Context())
        self.assertEqual(handler_db.partition("spot#i-app"), [])
        self.assertTrue(self.db.partition("spot#i-app"))
        with patch("boto3.resource") as resource:
            REAL_TABLE.__wrapped__()
        self.assertIs(resource.call_args.kwargs["config"], spot_status.SDK_CONFIG)

    # --- Other paths stay untouched -------------------------------------------

    def test_scheduled_probe_and_synthetic_do_not_call_ec2(self):
        with patch.object(h, "track_http_status"):
            self.assertEqual(h.handler({"source": "aws.events",
                                        "detail-type": "Scheduled Event"}),
                             {"http_up": True})
        self.assertEqual(h.handler({"source": "gmsv5.manual-test", "test_mode": True,
                                    "id": "smoke"}), {"synthetic": True, "new": True})
        self.assertEqual(self.ec2.calls, [])
        self.assertEqual(self.metrics[0][0][:2], ("HTTPUp", 1))
        self.assertEqual(self.db.partition("spot#i-app"), [])


if __name__ == "__main__":
    unittest.main()
