"""Spot status collector tests. No AWS calls: EC2 and DynamoDB are fakes."""
import os as _os, sys as _sys  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import no_network  # noqa: E402,F401  Blocks real network access (no AWS calls).
import sys
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from botocore.exceptions import ClientError, ConnectTimeoutError

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambda"))

import spot_status as s  # noqa: E402
from fake_dynamodb import FakeTable  # noqa: E402

IID = "i-app"
SIR = "sir-test0001"
STOP = "2026-11-20T10:00:00Z"
INCIDENT = STOP + "#evt-stop"


def utc(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def fixed_now(text="2026-11-20T10:00:05Z"):
    return lambda: utc(text)


def throttle():
    return ClientError({"Error": {"Code": "RequestLimitExceeded", "Message": "slow down"}},
                       "DescribeSpotInstanceRequests")


class FakeEC2:
    """Returns one Spot status per call from a list. Records every call."""

    def __init__(self, statuses, *, launch="2026-11-20T08:00:00Z", request_id=SIR,
                 state="stopped", request_instance=IID):
        self.statuses = list(statuses)
        self.launch = launch
        self.request_id = request_id
        self.state = state
        self.request_instance = request_instance
        self.calls = []
        self.before_spot_call = None

    def describe_instances(self, **kwargs):
        self.calls.append(("describe_instances", kwargs))
        instance = {
            "InstanceId": IID,
            "InstanceType": "t4g.small",
            "InstanceLifecycle": "spot",
            "State": {"Name": self.state},
            "LaunchTime": utc(self.launch),
            "Placement": {"AvailabilityZone": "ap-northeast-1a"},
            "StateReason": {"Code": "Server.SpotInstanceShutdown",
                            "Message": "Server.SpotInstanceShutdown: Spot"},
        }
        if self.request_id:
            instance["SpotInstanceRequestId"] = self.request_id
        return {"Reservations": [{"Instances": [instance]}]}

    def describe_spot_instance_requests(self, **kwargs):
        self.calls.append(("describe_spot_instance_requests", kwargs))
        if self.before_spot_call:
            self.before_spot_call()
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if isinstance(status, Exception):
            raise status
        code, updated = status
        return {"SpotInstanceRequests": [{
            "SpotInstanceRequestId": SIR,
            "InstanceId": self.request_instance,
            "State": "disabled" if code.startswith("instance-stopped") else "active",
            "SpotPrice": "0.021600",
            "InstanceInterruptionBehavior": "stop",
            "Status": {"Code": code, "Message": f"message for {code}",
                       "UpdateTime": utc(updated)},
        }]}


def stopped_row(db, *, stop=STOP, key=INCIDENT, window="2026-11-20T09:50:00Z",
                running=None):
    row = {"instance_id": IID, "event_time": key, "event_type": "stopped",
           "stop_at": stop, "spot_window_start": window, "spot_reason": "UNKNOWN",
           "spot_reason_confirmed": False}
    if running:
        row["running_at"] = running
    db.items[(IID, key)] = row
    return row


def obs(code, updated, *, observed="2026-11-20T10:00:05.000Z",
        launch="2026-11-20T08:00:00Z", request=SIR):
    return {"classification": s.classify(code), "status_code": code,
            "status_message": "m", "status_update_time": updated,
            "observed_at": observed, "spot_request_id": request, "launch_time": launch}


EVENT = {"id": "evt-stop", "time": STOP}


class NoRealAWS(unittest.TestCase):
    def setUp(self):
        guard = patch("boto3.client", side_effect=AssertionError("real AWS client"))
        guard.start()
        self.addCleanup(guard.stop)


class ClassificationTests(NoRealAWS):
    def test_documented_codes(self):
        self.assertEqual(s.classify("instance-stopped-no-capacity"), "CAPACITY")
        self.assertEqual(s.classify("instance-stopped-by-price"), "PRICE")
        self.assertEqual(s.classify("instance-stopped-by-user"), "USER")
        self.assertEqual(s.classify("instance-stopped-by-experiment"), "EXPERIMENT")

    def test_pending_and_unknown(self):
        self.assertEqual(s.classify("marked-for-stop"), "PENDING")
        self.assertEqual(s.classify("fulfilled"), "UNKNOWN")
        self.assertEqual(s.classify("instance-terminated-no-capacity"), "UNKNOWN")
        self.assertEqual(s.classify("some-future-code"), "UNKNOWN")
        self.assertEqual(s.classify(None), "UNKNOWN")

    def test_unknown_code_is_preserved_raw(self):
        client = FakeEC2([("some-future-code", "2026-11-20T10:00:00Z")])
        result = s.observe(IID, client=client, now=fixed_now())
        self.assertEqual(result["classification"], "UNKNOWN")
        self.assertEqual(result["status_code"], "some-future-code")


class ObserveTests(NoRealAWS):
    def test_collects_required_fields(self):
        client = FakeEC2([("instance-stopped-no-capacity", "2026-11-20T09:59:58Z")])
        result = s.observe(IID, client=client, now=fixed_now())
        self.assertEqual(result["spot_request_id"], SIR)
        self.assertEqual(result["status_code"], "instance-stopped-no-capacity")
        self.assertEqual(result["status_message"], "message for instance-stopped-no-capacity")
        self.assertEqual(result["status_update_time"], "2026-11-20T09:59:58Z")
        self.assertEqual(result["observed_at"], "2026-11-20T10:00:05.000Z")
        self.assertEqual(result["request_state"], "disabled")
        self.assertEqual(result["max_price"], "0.021600")
        self.assertEqual(result["interruption_behavior"], "stop")
        self.assertEqual(result["instance_type"], "t4g.small")
        self.assertEqual(result["availability_zone"], "ap-northeast-1a")
        self.assertEqual(result["state_reason_code"], "Server.SpotInstanceShutdown")
        self.assertIn("state_reason_message", result)
        self.assertEqual(result["classification"], "CAPACITY")
        self.assertNotIn("error", result)
        self.assertEqual(client.calls[1][1], {"SpotInstanceRequestIds": [SIR]})

    def test_missing_spot_request_id(self):
        client = FakeEC2([("fulfilled", STOP)], request_id=None)
        result = s.observe(IID, client=client, now=fixed_now())
        self.assertEqual(result["error"], "missing_spot_request_id")
        self.assertEqual(result["classification"], "UNKNOWN")
        self.assertEqual(len(client.calls), 1)

    def test_throttling_is_recorded_not_raised(self):
        client = FakeEC2([throttle()])
        result = s.observe(IID, client=client, now=fixed_now())
        self.assertEqual(result["error"], "RequestLimitExceeded")
        self.assertEqual(result["classification"], "UNKNOWN")
        self.assertIn("observed_at", result)

    def test_network_timeout_is_recorded_not_raised(self):
        client = FakeEC2([ConnectTimeoutError(endpoint_url="https://ec2.example")])
        result = s.observe(IID, client=client, now=fixed_now())
        self.assertEqual(result["error"], "ConnectTimeoutError")

    def test_request_for_another_instance_is_rejected(self):
        client = FakeEC2([("instance-stopped-no-capacity", STOP)], request_instance="i-other")
        result = s.observe(IID, client=client, now=fixed_now())
        self.assertEqual(result["error"], "spot_request_instance_mismatch")
        self.assertNotIn("status_code", result)

    def test_sdk_timeouts_and_retries_are_bounded(self):
        self.assertEqual(s.SDK_CONFIG.connect_timeout, 2)
        self.assertEqual(s.SDK_CONFIG.read_timeout, 3)
        self.assertEqual(s.SDK_CONFIG.retries, {"max_attempts": 2, "mode": "standard"})


class WindowTests(NoRealAWS):
    def incident(self, **extra):
        row = {"event_type": "stopped", "stop_at": STOP,
               "spot_window_start": "2026-11-20T09:50:00Z"}
        row.update(extra)
        return row

    def test_in_window(self):
        self.assertEqual(s.attribute(obs("instance-stopped-no-capacity",
                                         "2026-11-20T09:59:58Z"), self.incident()),
                         "in_window")

    def test_status_older_than_current_run_is_stale(self):
        # Previous interruption's status, still shown after a quick restart.
        observation = obs("instance-stopped-no-capacity", "2026-11-20T09:55:00Z",
                          launch="2026-11-20T09:57:00Z")
        self.assertEqual(s.attribute(observation, self.incident()), "stale")

    def test_status_before_window_is_stale(self):
        self.assertEqual(s.attribute(obs("instance-stopped-no-capacity",
                                         "2026-11-20T09:40:00Z"), self.incident()),
                         "stale")

    def test_status_after_incident_resumed_is_rejected(self):
        incident = self.incident(running_at="2026-11-20T10:03:00Z")
        observation = obs("instance-stopped-no-capacity", "2026-11-20T10:20:00Z",
                          observed="2026-11-20T10:20:05.000Z")
        self.assertEqual(s.attribute(observation, incident), "after_incident")

    def test_status_after_later_launch_is_rejected(self):
        observation = obs("instance-stopped-by-user", "2026-11-20T10:30:00Z",
                          observed="2026-11-20T10:31:00.000Z",
                          launch="2026-11-20T10:05:00Z")
        self.assertEqual(s.attribute(observation, self.incident()), "after_incident")

    def test_legacy_incident_is_never_backfilled(self):
        incident = {"event_type": "stopped", "stop_at": STOP}
        self.assertEqual(s.attribute(obs("instance-stopped-no-capacity", STOP), incident),
                         "legacy_incident")

    def test_other_spot_request_is_rejected(self):
        incident = self.incident(spot_request_id="sir-other")
        self.assertEqual(s.attribute(obs("instance-stopped-no-capacity", STOP), incident),
                         "request_mismatch")


class EvidenceWriteTests(NoRealAWS):
    def setUp(self):
        super().setUp()
        self.db = FakeTable()
        stopped_row(self.db)

    def row(self):
        return self.db.row(IID, INCIDENT)

    def test_unresolved_then_confirmed(self):
        self.assertEqual(s.apply_to_incident(self.db, IID, INCIDENT,
                                             obs("marked-for-stop", "2026-11-20T09:58:00Z"),
                                             "stopped"), "unresolved")
        self.assertEqual(self.row()["spot_reason"], "PENDING")
        self.assertEqual(s.apply_to_incident(
            self.db, IID, INCIDENT,
            obs("instance-stopped-no-capacity", "2026-11-20T09:59:58Z"), "stopped"),
            "confirmed")
        self.assertEqual(self.row()["spot_reason"], "CAPACITY")
        self.assertTrue(self.row()["spot_reason_confirmed"])

    def test_fulfilled_cannot_overwrite_confirmed(self):
        s.apply_to_incident(self.db, IID, INCIDENT,
                            obs("instance-stopped-no-capacity", "2026-11-20T09:59:58Z"),
                            "stopped")
        result = s.apply_to_incident(self.db, IID, INCIDENT,
                                     obs("fulfilled", "2026-11-20T10:03:00Z"), "running")
        self.assertEqual(result, "superseded")
        self.assertEqual(self.row()["spot_status_code"], "instance-stopped-no-capacity")
        self.assertEqual(self.row()["spot_reason"], "CAPACITY")

    def test_conflicting_authoritative_code_is_kept_and_reported(self):
        s.apply_to_incident(self.db, IID, INCIDENT,
                            obs("instance-stopped-no-capacity", "2026-11-20T09:59:58Z"),
                            "stopped")
        result = s.apply_to_incident(self.db, IID, INCIDENT,
                                     obs("instance-stopped-by-user", "2026-11-20T09:59:59Z"),
                                     "stopped")
        self.assertEqual(result, "conflict")
        self.assertEqual(self.row()["spot_reason"], "CAPACITY")
        self.assertTrue(self.row()["spot_reason_conflict"])
        self.assertEqual(self.row()["spot_conflict_codes"], {"instance-stopped-by-user"})
        self.assertIn("CONFLICT", s.incident_summary(self.row()))

    def test_same_authoritative_code_is_duplicate(self):
        evidence = obs("instance-stopped-no-capacity", "2026-11-20T09:59:58Z")
        s.apply_to_incident(self.db, IID, INCIDENT, evidence, "stopped")
        self.assertEqual(s.apply_to_incident(self.db, IID, INCIDENT, evidence, "running"),
                         "duplicate")
        self.assertNotIn("spot_reason_conflict", self.row())

    def test_older_unresolved_status_does_not_replace_newer(self):
        s.apply_to_incident(self.db, IID, INCIDENT,
                            obs("fulfilled", "2026-11-20T10:02:00Z"), "stopped")
        result = s.apply_to_incident(self.db, IID, INCIDENT,
                                     obs("marked-for-stop", "2026-11-20T09:58:00Z"),
                                     "warning")
        self.assertEqual(result, "superseded")
        self.assertEqual(self.row()["spot_status_code"], "fulfilled")

    def test_missing_incident_row_is_not_created(self):
        result = s.apply_to_incident(self.db, IID, "2026-11-20T11:00:00Z#missing",
                                     obs("instance-stopped-no-capacity", STOP), "stopped")
        self.assertEqual(result, "no_incident")
        self.assertIsNone(self.db.row(IID, "2026-11-20T11:00:00Z#missing"))

    def test_concurrent_unknown_writers_cannot_replace_confirmed(self):
        confirmed = obs("instance-stopped-no-capacity", "2026-11-20T09:59:58Z")
        unresolved = [obs("fulfilled", f"2026-11-20T10:0{n}:00Z") for n in range(1, 10)]
        start = threading.Barrier(len(unresolved) + 1)

        def write(evidence):
            start.wait()
            s.apply_to_incident(self.db, IID, INCIDENT, evidence, "running")

        threads = [threading.Thread(target=write, args=(item,))
                   for item in unresolved + [confirmed]]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.row()["spot_reason"], "CAPACITY")
        self.assertTrue(self.row()["spot_reason_confirmed"])

    def test_concurrent_conflicting_writers_keep_one_and_report_other(self):
        codes = ["instance-stopped-no-capacity", "instance-stopped-by-user"]
        start = threading.Barrier(2)
        results = []

        def write(code):
            start.wait()
            results.append(s.apply_to_incident(
                self.db, IID, INCIDENT, obs(code, "2026-11-20T09:59:58Z"), "stopped"))

        threads = [threading.Thread(target=write, args=(code,)) for code in codes]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results), ["confirmed", "conflict"])
        kept = self.row()["spot_status_code"]
        self.assertIn(kept, codes)
        self.assertEqual(self.row()["spot_conflict_codes"], set(codes) - {kept})


class CollectTests(NoRealAWS):
    def setUp(self):
        super().setUp()
        self.db = FakeTable()
        stopped_row(self.db)
        self.sleeps = []

    def collect(self, client, context=None):
        return s.collect(self.db, IID, phase="stopped", event=EVENT,
                         incident_key=INCIDENT, client=client, context=context,
                         retries=True, sleep=self.sleeps.append, clock=lambda: 0.0,
                         now=fixed_now())

    def observations(self):
        return self.db.partition("spot#" + IID)

    def test_retries_until_confirmed_and_persists_each_attempt(self):
        client = FakeEC2([("marked-for-stop", "2026-11-20T09:58:00Z"),
                          ("instance-stopped-no-capacity", "2026-11-20T09:59:58Z")])
        seen_before_second_call = []
        client.before_spot_call = lambda: seen_before_second_call.append(
            len(self.observations()))
        results = self.collect(client)
        self.assertEqual([r["result"] for r in results], ["unresolved", "confirmed"])
        self.assertEqual(self.sleeps, [2])
        self.assertEqual(seen_before_second_call, [0, 1])  # first row saved first
        rows = self.observations()
        self.assertEqual([row["status_code"] for row in rows],
                         ["marked-for-stop", "instance-stopped-no-capacity"])
        self.assertEqual(self.db.row(IID, INCIDENT)["spot_reason"], "CAPACITY")

    def test_three_bounded_attempts_when_unresolved(self):
        client = FakeEC2([("marked-for-stop", "2026-11-20T09:58:00Z")])
        results = self.collect(client)
        self.assertEqual(len(results), 3)
        self.assertEqual(self.sleeps, [2, 5])
        self.assertEqual(self.db.row(IID, INCIDENT)["spot_reason"], "PENDING")
        self.assertFalse(self.db.row(IID, INCIDENT)["spot_reason_confirmed"])

    def test_api_failures_leave_reason_unknown(self):
        client = FakeEC2([throttle()])
        results = self.collect(client)
        self.assertEqual([r["result"] for r in results], ["unavailable"] * 3)
        self.assertEqual(self.db.row(IID, INCIDENT)["spot_reason"], "UNKNOWN")
        self.assertEqual({row["error"] for row in self.observations()},
                         {"RequestLimitExceeded"})

    def test_throttling_then_success(self):
        client = FakeEC2([throttle(), ("instance-stopped-by-price", "2026-11-20T09:59:58Z")])
        results = self.collect(client)
        self.assertEqual([r["result"] for r in results], ["unavailable", "confirmed"])
        self.assertEqual(self.db.row(IID, INCIDENT)["spot_reason"], "PRICE")

    def test_insufficient_lambda_time_skips_retries(self):
        class Context:
            def get_remaining_time_in_millis(self):
                return s.ATTEMPT_WORST_MS + s.SAFETY_MS + 1000

        client = FakeEC2([("marked-for-stop", "2026-11-20T09:58:00Z")])
        results = self.collect(client, context=Context())
        self.assertEqual([r["result"] for r in results],
                         ["unresolved", "skipped_time_budget"])
        self.assertEqual(self.sleeps, [])

    def test_no_time_skips_even_first_call(self):
        class Context:
            def get_remaining_time_in_millis(self):
                return 5000

        client = FakeEC2([("instance-stopped-no-capacity", STOP)])
        results = self.collect(client, context=Context())
        self.assertEqual(results[0]["result"], "skipped_time_budget")
        self.assertEqual(client.calls, [])

    def test_duplicate_event_delivery_writes_no_new_observation(self):
        client = FakeEC2([("instance-stopped-no-capacity", "2026-11-20T09:59:58Z")])
        self.collect(client)
        again = self.collect(client)
        self.assertEqual(again[0]["result"], "duplicate_observation")
        self.assertEqual(len(self.observations()), 1)

    def test_observation_rows_have_ttl_and_raw_codes(self):
        self.collect(FakeEC2([("fulfilled", "2026-11-20T08:00:00Z")]))
        row = self.observations()[0]
        self.assertEqual(row["expires_at"], int(utc(STOP).timestamp()) + 400 * 86400)
        self.assertEqual(row["status_code"], "fulfilled")
        self.assertEqual(row["classification"], "UNKNOWN")
        self.assertEqual(row["attribution"], "stale")
        self.assertFalse(row["authoritative"])


class IncidentContextTests(NoRealAWS):
    def setUp(self):
        super().setUp()
        self.db = FakeTable()
        self.db.items[(IID, INCIDENT)] = {"instance_id": IID, "event_time": INCIDENT,
                                          "event_type": "stopped", "stop_at": STOP}

    def test_window_starts_after_previous_run_and_signals_are_separate(self):
        prior = {"last_running": "2026-11-20T09:55:00Z",
                 "last_interruption_warning": "2026-11-20T09:58:00Z",
                 "last_rebalance_recommendation": "2026-11-20T09:56:00Z",
                 "last_stopping": "2026-11-20T09:59:50Z",
                 "last_spot_warning": "2026-11-20T09:58:00Z"}
        s.init_incident(self.db, IID, INCIDENT, stop_at=STOP, prior_state=prior)
        row = self.db.row(IID, INCIDENT)
        self.assertEqual(row["spot_window_start"], "2026-11-20T09:55:00Z")
        self.assertEqual(row["spot_previous_running_at"], "2026-11-20T09:55:00Z")
        self.assertEqual(row["interruption_warning_at"], "2026-11-20T09:58:00Z")
        self.assertEqual(row["rebalance_recommendation_at"], "2026-11-20T09:56:00Z")
        self.assertEqual(row["stopping_at"], "2026-11-20T09:59:50Z")
        self.assertEqual(row["stop_at"], STOP)  # Stop-to-Recovery start is unchanged.
        self.assertEqual(row["spot_reason"], "UNKNOWN")

    def test_signals_from_previous_cycle_are_not_attached(self):
        prior = {"last_running": "2026-11-20T09:55:00Z",
                 "last_interruption_warning": "2026-11-20T09:54:00Z",
                 "last_stopping": "2026-11-20T09:54:30Z"}
        s.init_incident(self.db, IID, INCIDENT, stop_at=STOP, prior_state=prior)
        row = self.db.row(IID, INCIDENT)
        self.assertNotIn("interruption_warning_at", row)
        self.assertNotIn("stopping_at", row)

    def test_signal_before_previous_stop_is_not_attached(self):
        # Previous running event lost: the window floor is soft (10 minutes).
        prior = {"last_stopped": "2026-11-20T09:56:00Z",
                 "last_stopping": "2026-11-20T09:55:30Z"}
        s.init_incident(self.db, IID, INCIDENT, stop_at=STOP, prior_state=prior)
        row = self.db.row(IID, INCIDENT)
        self.assertEqual(row["spot_window_start"], "2026-11-20T09:50:00Z")
        self.assertEqual(row["spot_previous_stop_at"], "2026-11-20T09:56:00Z")
        self.assertNotIn("stopping_at", row)

    def test_init_does_not_replace_confirmed_evidence(self):
        row = self.db.row(IID, INCIDENT)
        row.update(spot_reason="CAPACITY", spot_reason_confirmed=True)
        s.init_incident(self.db, IID, INCIDENT, stop_at=STOP, prior_state={})
        self.assertEqual(self.db.row(IID, INCIDENT)["spot_reason"], "CAPACITY")
        self.assertTrue(self.db.row(IID, INCIDENT)["spot_reason_confirmed"])

    def test_newer_signal_state_is_not_replaced_by_older_delivery(self):
        s.record_signal_state(self.db, IID, "warning", "2026-11-20T10:00:00Z", "~STATE")
        s.record_signal_state(self.db, IID, "warning", "2026-11-20T09:00:00Z", "~STATE")
        self.assertEqual(self.db.row(IID, "~STATE")["last_interruption_warning"],
                         "2026-11-20T10:00:00Z")


class AssociationEdgeTests(NoRealAWS):
    """Rapid cycles, delayed or missing events, overlapping windows."""

    A = {"event_time": "2026-11-20T10:00:00Z#a", "event_type": "stopped",
         "stop_at": "2026-11-20T10:00:00Z", "spot_window_start": "2026-11-20T09:50:00Z"}
    B = {"event_time": "2026-11-20T10:06:00Z#b", "event_type": "stopped",
         "stop_at": "2026-11-20T10:06:00Z", "spot_window_start": "2026-11-20T09:56:00Z",
         "spot_previous_stop_at": "2026-11-20T10:00:00Z"}

    def incident(self, base, **extra):
        row = dict(base)
        row.update(extra)
        return row

    def test_next_interruption_inside_old_60s_grace_is_not_attached(self):
        # A restarted at 10:02:00; B's stop status was set 20 s later.
        a = self.incident(self.A, running_at="2026-11-20T10:02:00Z")
        later = obs("instance-stopped-by-user", "2026-11-20T10:02:20Z",
                    observed="2026-11-20T10:02:45.000Z", launch="2026-11-20T10:02:00Z")
        self.assertEqual(s.attribute(later, a), "after_incident")

    def test_restart_bound_holds_when_running_event_is_missing(self):
        later = obs("instance-stopped-by-user", "2026-11-20T10:02:20Z",
                    observed="2026-11-20T10:02:45.000Z", launch="2026-11-20T10:02:00Z")
        self.assertEqual(s.attribute(later, self.incident(self.A)), "after_incident")

    def test_own_status_before_restart_is_still_attached(self):
        a = self.incident(self.A, running_at="2026-11-20T10:02:00Z")
        own = obs("instance-stopped-no-capacity", "2026-11-20T10:01:59Z",
                  observed="2026-11-20T10:02:45.000Z", launch="2026-11-20T10:02:00Z")
        self.assertEqual(s.attribute(own, a), "in_window")

    def test_overlapping_windows_without_restart_evidence_are_ambiguous(self):
        # A's running event and every launch time are unknown.
        unclear = obs("instance-stopped-no-capacity", "2026-11-20T10:00:30Z",
                      observed="2026-11-20T10:06:05.000Z", launch=None)
        self.assertEqual(s.attribute(unclear, self.incident(self.B), [self.incident(self.A)]),
                         "ambiguous")
        self.assertEqual(s.attribute(unclear, self.incident(self.A), [self.incident(self.B)]),
                         "ambiguous")

    def test_known_run_start_resolves_overlap(self):
        b = self.incident(self.B, spot_run_started_at="2026-11-20T10:03:00Z")
        old = obs("instance-stopped-no-capacity", "2026-11-20T10:00:30Z",
                  observed="2026-11-20T10:06:05.000Z", launch=None)
        new = obs("instance-stopped-by-user", "2026-11-20T10:05:50Z",
                  observed="2026-11-20T10:06:05.000Z", launch=None)
        self.assertEqual(s.attribute(old, b, [self.incident(self.A)]), "stale")
        self.assertEqual(s.attribute(new, b, [self.incident(self.A)]), "in_window")
        # From A's side, B's known run start ends A's window.
        self.assertEqual(s.attribute(new, self.incident(self.A), [b]), "after_incident")

    def test_later_restart_without_next_run_start_is_ambiguous(self):
        # Two restarts since A; only the last launch (10:08) is visible.
        between = obs("instance-stopped-by-user", "2026-11-20T10:04:00Z",
                      observed="2026-11-20T10:09:00.000Z", launch="2026-11-20T10:08:00Z")
        self.assertEqual(s.attribute(between, self.incident(self.A),
                                     [self.incident(self.B)]), "ambiguous")

    def test_ambiguous_observation_is_kept_but_confirms_nothing(self):
        db = FakeTable()
        db.items[(IID, self.B["event_time"])] = dict(self.B, instance_id=IID,
                                                     spot_reason="UNKNOWN",
                                                     spot_reason_confirmed=False)
        db.items[(IID, self.A["event_time"])] = dict(self.A, instance_id=IID)
        client = FakeEC2([throttle()])
        client.statuses = [("instance-stopped-no-capacity", "2026-11-20T10:00:30Z")]
        unclear = s.observe(IID, client=client, now=fixed_now("2026-11-20T10:06:05Z"))
        unclear["launch_time"] = None
        result = s.record(db, IID, unclear, phase="stopped",
                          event={"id": "b", "time": self.B["stop_at"]}, attempt=0,
                          incident_key=self.B["event_time"])
        self.assertEqual(result["result"], "ambiguous")
        row = db.partition("spot#" + IID)[0]
        self.assertEqual(row["attribution"], "ambiguous")
        self.assertEqual(row["status_code"], "instance-stopped-no-capacity")
        self.assertFalse(db.row(IID, self.B["event_time"])["spot_reason_confirmed"])
        self.assertEqual(db.row(IID, self.B["event_time"])["spot_reason"], "UNKNOWN")

    def test_signal_goes_to_first_stop_after_it(self):
        late_stopping = "2026-11-20T09:59:30Z"
        self.assertFalse(s.signal_belongs(late_stopping, self.incident(self.B)))
        self.assertTrue(s.signal_belongs(late_stopping, self.incident(self.A)))
        b_without_previous = self.incident(self.B)
        del b_without_previous["spot_previous_stop_at"]
        self.assertFalse(s.signal_belongs(late_stopping, b_without_previous,
                                          [self.incident(self.A)]))


class WorstCaseWorld:
    """Virtual Lambda clock: every AWS call takes the worst case."""

    def __init__(self, remaining_ms):
        self.remaining = remaining_ms
        self.at_call = []

    def get_remaining_time_in_millis(self):
        return self.remaining

    def slow(self, target):
        world = self

        class Slow:
            def __getattr__(self, name):
                method = getattr(target, name)

                def call(*args, **kwargs):
                    world.at_call.append(world.remaining)
                    world.remaining -= s.CALL_WORST_MS
                    return method(*args, **kwargs)

                return call

        return Slow()

    def sleep(self, seconds):
        self.remaining -= seconds * 1000

    def clock(self):
        return -self.remaining / 1000


class BudgetTests(NoRealAWS):
    def test_worst_case_never_starts_a_call_it_cannot_finish(self):
        for remaining in (89000, 60000, 45000, 20000, 5000):
            with self.subTest(remaining=remaining):
                db = FakeTable()
                stopped_row(db)
                world = WorstCaseWorld(remaining)
                s.on_stopped(world.slow(db), IID, EVENT, INCIDENT, {}, context=world,
                             client=world.slow(FakeEC2([("marked-for-stop",
                                                         "2026-11-20T09:58:00Z")])),
                             sleep=world.sleep, clock=world.clock)
                for left in world.at_call:
                    self.assertGreaterEqual(left, s.CALL_WORST_MS + s.SAFETY_MS)
                self.assertGreaterEqual(world.remaining, s.SAFETY_MS)

    def test_full_lambda_budget_still_reads_and_persists_one_observation(self):
        db = FakeTable()
        stopped_row(db)
        world = WorstCaseWorld(88000)
        results = s.on_stopped(world.slow(db), IID, EVENT, INCIDENT, {}, context=world,
                               client=world.slow(FakeEC2([("marked-for-stop",
                                                           "2026-11-20T09:58:00Z")])),
                               sleep=world.sleep, clock=world.clock)
        self.assertEqual(len(db.partition("spot#" + IID)), 1)
        self.assertEqual(results[-1]["result"], "skipped_time_budget")

    def test_constants_match_sdk_configuration(self):
        self.assertEqual(s.CALL_WORST_MS, 16000)
        self.assertEqual(s.ATTEMPT_WORST_MS, 48000)


class SummaryTests(NoRealAWS):
    def test_confirmed_summary(self):
        text = s.incident_summary({"spot_reason": "CAPACITY", "spot_reason_confirmed": True,
                                   "spot_status_code": "instance-stopped-no-capacity",
                                   "spot_status_update_time": "2026-11-20T09:59:58Z",
                                   "spot_evidence_observed_at": "2026-11-20T10:00:05.000Z"})
        self.assertIn("CAPACITY (confirmed by AWS Spot request status)", text)
        self.assertIn("instance-stopped-no-capacity", text)
        self.assertIn("2026-11-20T10:00:05.000Z", text)

    def test_unresolved_summary_is_not_presented_as_confirmed(self):
        text = s.incident_summary({"spot_reason": "PENDING", "spot_reason_confirmed": False,
                                   "spot_status_code": "marked-for-stop",
                                   "spot_evidence_observed_at": "2026-11-20T10:00:05.000Z"})
        self.assertIn("PENDING (not confirmed)", text)
        self.assertNotIn("confirmed by", text)

    def test_historical_row_summary(self):
        self.assertIn("UNKNOWN (no AWS Spot status evidence",
                      s.incident_summary({"stop_at": STOP}))

    def test_conflict_summary_lists_other_codes(self):
        text = s.incident_summary({"spot_reason": "CAPACITY", "spot_reason_confirmed": True,
                                   "spot_status_code": "instance-stopped-no-capacity",
                                   "spot_conflict_codes": {"instance-stopped-by-user"}})
        self.assertIn("CONFLICT also observed: instance-stopped-by-user", text)


if __name__ == "__main__":
    unittest.main()
