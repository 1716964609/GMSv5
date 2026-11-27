"""Burn rate alarm notification tests. No AWS calls.

DynamoDB is the in-memory FakeTable (real condition and update expressions).
CloudWatch alarm evaluation itself cannot be tested locally: the M-of-N tests
below check the Terraform values against a model of complete-data
evaluation, not CloudWatch.
"""
import importlib.util
import json
import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "lambda"))

import burn_rate as br  # noqa: E402
from fake_dynamodb import FakeTable  # noqa: E402

NAME = "gmsv5-availability-burn-rate-fast"
ARN = "arn:aws:cloudwatch:ap-northeast-1:000000000000:alarm:" + NAME
KEY = ("burnrate#" + NAME, "~STATE")


def reason_data(failed, listed=60, threshold=1.0):
    points = [0.0] * failed + [1.0] * (listed - failed)
    return json.dumps({"version": "1.0", "queryDate": "2026-11-24T00:06:40.985+0000",
                       "startDate": "2026-11-23T23:06:00.000+0000", "statistic": "Minimum",
                       "period": 60, "recentDatapoints": points, "threshold": threshold})


def alarm_event(value, ts, previous="OK", failed=None, listed=60, reason=None,
                event_id=None, name=NAME, arn=ARN, data=None):
    state = {"value": value, "timestamp": ts,
             "reason": reason if reason is not None else "Threshold Crossed: test"}
    if data is not None:
        state["reasonData"] = data
    elif failed is not None:
        state["reasonData"] = reason_data(failed, listed)
    return {
        "version": "0", "id": event_id or f"evt-{value}-{ts}",
        "detail-type": "CloudWatch Alarm State Change", "source": "aws.cloudwatch",
        "account": "000000000000", "time": ts[:19] + "Z", "region": "ap-northeast-1",
        "resources": [arn],
        "detail": {"alarmName": name, "state": state,
                   "previousState": {"value": previous, "reason": "x",
                                     "timestamp": "2026-11-23T00:00:00.000+0000"},
                   "configuration": {"metrics": []}},
    }


T1 = "2026-11-24T00:06:40.989+0000"   # ALARM
T2 = "2026-11-24T01:02:41.120+0000"   # OK
T3 = "2026-11-24T05:06:40.500+0000"   # ALARM again
BASE = datetime(2026, 11, 24, 0, 7, tzinfo=timezone.utc)


class Harness:
    def __init__(self, delivery="enabled"):
        self.table = FakeTable()
        self.sent = []
        self.clock = BASE
        self.delivery = delivery
        self.fail = 0  # number of upcoming notify() calls that fail
        self.during_notify = None

    def notify(self, text):
        if self.fail:
            self.fail -= 1
            raise OSError("relay unavailable")
        if self.during_notify:
            hook, self.during_notify = self.during_notify, None
            hook()
        self.sent.append(text)

    def run(self, event):
        return br.handle(event, table=self.table, notify=self.notify,
                         now=lambda: self.clock, alarm_name=NAME, alarm_arn=ARN,
                         delivery=self.delivery)

    def row(self):
        return self.table.row(*KEY) or {}


class ParseTests(unittest.TestCase):
    def test_accepts_exact_alarm(self):
        change = br.parse(alarm_event("ALARM", T1, failed=9), alarm_name=NAME, alarm_arn=ARN)
        self.assertEqual(change["alarm_state"], "ALARM")
        self.assertEqual(change["state_at"], "2026-11-24T00:06:40.989Z")
        self.assertEqual(change["previous_state"], "OK")
        self.assertFalse([k for k in change if k.startswith("observed")])

    def test_rejects_other_sources_types_names_and_arns(self):
        event = alarm_event("ALARM", T1)
        cases = {
            "unsupported event": dict(event, source="aws.ec2"),
            "unmonitored alarm": alarm_event("ALARM", T1, name="other-alarm"),
            "unmonitored alarm ARN": alarm_event("ALARM", T1, arn=ARN + "-x"),
        }
        cases["unsupported event "] = dict(event, **{"detail-type": "CloudWatch Alarm Configuration Change"})
        for expected, evt in cases.items():
            self.assertEqual(br.parse(evt, alarm_name=NAME, alarm_arn=ARN), expected.strip())
        extra = dict(event, resources=[ARN, ARN + "-x"])
        self.assertEqual(br.parse(extra, alarm_name=NAME, alarm_arn=ARN), "unmonitored alarm ARN")

    def test_unconfigured_name_or_arn_ignores_everything(self):
        event = alarm_event("ALARM", T1)
        self.assertEqual(br.parse(event, alarm_name="", alarm_arn=ARN), "unmonitored alarm")
        self.assertEqual(br.parse(event, alarm_name=NAME, alarm_arn=""), "unmonitored alarm ARN")

    def test_rejects_unknown_state(self):
        self.assertEqual(br.parse(alarm_event("PENDING", T1), alarm_name=NAME, alarm_arn=ARN),
                         "unsupported alarm state")

    def test_timestamp_forms(self):
        self.assertEqual(br.state_time("2026-11-24T00:06:40.989+0000"), "2026-11-24T00:06:40.989Z")
        self.assertEqual(br.state_time("2026-11-24T09:06:40.5+09:00"), "2026-11-24T00:06:40.500Z")
        self.assertEqual(br.state_time("2026-11-24T00:06:40Z"), "2026-11-24T00:06:40.000Z")
        for bad in (None, "", "yesterday", "2026-11-24 00:06:40", 1732406800):
            self.assertIsNone(br.state_time(bad))

    def test_falls_back_to_event_time_and_rejects_no_time(self):
        event = alarm_event("ALARM", T1)
        del event["detail"]["state"]["timestamp"]
        self.assertEqual(br.parse(event, alarm_name=NAME, alarm_arn=ARN)["state_at"],
                         "2026-11-24T00:06:40.000Z")
        event["time"] = "not a time"
        self.assertEqual(br.parse(event, alarm_name=NAME, alarm_arn=ARN),
                         "missing state timestamp")

    def test_unchecked_reason_kind(self):
        event = alarm_event("INSUFFICIENT_DATA", T1, reason="Unchecked: Initial alarm creation")
        self.assertEqual(br.parse(event, alarm_name=NAME, alarm_arn=ARN)["reason_kind"], "unchecked")


class ReasonDataTests(unittest.TestCase):
    """reasonData.recentDatapoints is not the evaluation window (Runbook §13.6), so it is ignored."""

    def test_reason_data_is_neither_stored_nor_quoted(self):
        h = Harness()
        h.run(alarm_event("ALARM", T1, failed=9))
        h.run(alarm_event("OK", T2, previous="ALARM", failed=8))
        self.assertFalse([k for k in h.row() if k.startswith("observed")])
        for text in h.sent:
            self.assertNotIn("Observed", text)
            self.assertNotRegex(text, r"\d+(\.\d+)?x\b")  # no burn rate multiple
            self.assertNotIn("SLO", text)
            self.assertNotIn("budget", text.lower())

    def test_malformed_reason_data_is_harmless(self):
        for data in (None, "", "{", '{"recentDatapoints": [0, NaN]}', "[]"):
            h = Harness()
            self.assertEqual(h.run(alarm_event("ALARM", T1, data=data))["sent"], ["alarm"], data)


class MessageTests(unittest.TestCase):
    CONTEXT = ["Now: DOWN — 4 min (since first failed check 21:10 JST); 5 consecutive failed checks",
               "Last 60 completed min (20:12 JST–21:12 JST): failed 9 / ok 51 / missing 0",
               "Pattern: 3 observed episodes, longest 5 min"]

    def item(self, state="ALARM", previous="OK"):
        return {"alarm_state": state, "state_at": "2026-11-24T00:06:40.989Z",
                "previous_state": previous}

    def test_alarm_message_contents(self):
        text = br.message("alarm", self.item(), NAME, self.CONTEXT)
        lines = text.split("\n")
        self.assertEqual(lines[0], "HIGH — Availability Burn Rate: ALARM")
        self.assertEqual(lines[1], "Alarm threshold: 9/60 failed datapoints")
        self.assertEqual(lines[2:5], self.CONTEXT)
        for part in ("sunlightjetrans.com", NAME, "OK -> ALARM at 09:06:40 JST",
                     "2026-11-24T00:06:40.989Z UTC, alarm state timestamp",
                     "Now = latest HTTP check when this message was generated"):
            self.assertIn(part, text)
        self.assertNotIn("14.4", text)
        self.assertLessEqual(len(text), 1800)

    def test_without_context_says_not_read(self):
        text = br.message("alarm", self.item(), NAME)
        self.assertIn("Now: UNKNOWN — HTTP state not read", text)
        self.assertIn("Last 60 completed min: unavailable (not read)", text)
        self.assertNotIn("Now: UP", text)

    def test_context_failure_keeps_the_message(self):
        class Broken:
            def lines(self, state_at):
                raise RuntimeError("boom")
        lines = br.context_lines(Broken(), self.item())
        self.assertEqual(lines, br.NO_CONTEXT)

    def test_normalized_does_not_claim_recovery(self):
        text = br.message("normalized", self.item("OK", previous="ALARM"), NAME, self.CONTEXT)
        self.assertTrue(text.startswith("HIGH — Availability Burn Rate: NORMALIZED (alarm back to OK)\n"))
        self.assertIn("ALARM -> OK", text)
        self.assertIn("CloudWatch alarm returned to OK", text)
        self.assertIn("NOT when the site recovered", text)
        self.assertIn("does not confirm it is up now", text)
        self.assertNotIn("RECOVERED", text.replace("HTTP DOWN/RECOVERED", ""))

    def test_insufficient_is_not_health_or_recovery(self):
        for kind in ("insufficient", "data_restored"):
            text = br.message(kind, self.item("INSUFFICIENT_DATA" if kind == "insufficient"
                                              else "OK"), NAME).lower()
            self.assertNotIn("healthy", text)
            self.assertNotIn("recovered", text)
            self.assertNotIn("normalized", text)
        text = br.message("insufficient", self.item("INSUFFICIENT_DATA"), NAME)
        self.assertTrue(text.startswith("WARN — Availability Burn Rate: DATA INSUFFICIENT\n"))
        self.assertIn("not proof of downtime or recovery", text)

    def test_headers_differ_per_kind(self):
        headers = {br.message(k, self.item(), NAME).split("\n")[0] for k in br.HEADERS}
        self.assertEqual(len(headers), 4)


class OwedTests(unittest.TestCase):
    def test_decision_table(self):
        cases = [
            ("ALARM", None, "evaluated", "alarm"),
            ("ALARM", "OK", "evaluated", "alarm"),
            ("ALARM", "INSUFFICIENT_DATA", "evaluated", "alarm"),
            ("ALARM", "ALARM", "evaluated", None),
            ("OK", None, "evaluated", None),
            ("OK", "OK", "evaluated", None),
            ("OK", "ALARM", "evaluated", "normalized"),
            ("OK", "INSUFFICIENT_DATA", "evaluated", "data_restored"),
            ("INSUFFICIENT_DATA", None, "evaluated", "insufficient"),
            ("INSUFFICIENT_DATA", "ALARM", "evaluated", "insufficient"),
            ("INSUFFICIENT_DATA", "INSUFFICIENT_DATA", "evaluated", None),
            ("INSUFFICIENT_DATA", None, "unchecked", None),
            ("INSUFFICIENT_DATA", "ALARM", "unchecked", None),
        ]
        for state, delivered, kind, expected in cases:
            item = {"alarm_state": state, "reason_kind": kind}
            if delivered:
                item["notified_state"] = delivered
            self.assertEqual(br.owed(item), expected, (state, delivered, kind))


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()

    def test_alarm_then_normalized(self):
        self.assertEqual(self.h.run(alarm_event("ALARM", T1, failed=9))["sent"], ["alarm"])
        self.h.clock += timedelta(minutes=56)
        result = self.h.run(alarm_event("OK", T2, previous="ALARM", failed=8))
        self.assertEqual(result["sent"], ["normalized"])
        self.assertEqual(len(self.h.sent), 2)
        self.assertIn("HIGH — Availability Burn Rate: NORMALIZED", self.h.sent[1])
        row = self.h.row()
        self.assertEqual((row["notified_state"], row["notified_at"]), ("OK", br.state_time(T2)))
        self.assertNotIn("lease_until", row)

    def test_duplicate_event_sends_once(self):
        event = alarm_event("ALARM", T1, failed=9)
        self.h.run(event)
        result = self.h.run(event)
        self.assertEqual(result, {"burn_rate": "duplicate", "alarm_state": "ALARM", "sent": []})
        self.assertEqual(len(self.h.sent), 1)

    def test_duplicate_with_different_event_id_sends_once(self):
        self.h.run(alarm_event("ALARM", T1, failed=9, event_id="a"))
        self.h.run(alarm_event("ALARM", T1, failed=9, event_id="b"))
        self.assertEqual(len(self.h.sent), 1)

    def test_out_of_order_old_alarm_does_not_overwrite_newer_ok(self):
        self.h.run(alarm_event("ALARM", T1, failed=9))
        self.h.run(alarm_event("OK", T2, previous="ALARM", failed=8))
        result = self.h.run(alarm_event("ALARM", T1, failed=9, event_id="late-copy"))
        self.assertEqual(result["burn_rate"], "stale")
        self.assertEqual(self.h.row()["alarm_state"], "OK")
        self.assertEqual(len(self.h.sent), 2)

    def test_ok_arriving_before_its_alarm(self):
        # OK (newer) arrives first: nothing was announced, so nothing to normalize.
        self.assertEqual(self.h.run(alarm_event("OK", T2, previous="ALARM", failed=8))["sent"], [])
        result = self.h.run(alarm_event("ALARM", T1, failed=9))
        self.assertEqual(result["burn_rate"], "stale")
        self.assertEqual(self.h.sent, [])
        self.assertEqual(self.h.row()["alarm_state"], "OK")

    def test_initial_insufficient_to_ok_is_silent(self):
        result = self.h.run(alarm_event("OK", T1, previous="INSUFFICIENT_DATA", failed=0))
        self.assertEqual(result["sent"], [])
        self.assertEqual(self.h.sent, [])

    def test_unchecked_insufficient_is_silent(self):
        event = alarm_event("INSUFFICIENT_DATA", T1, previous="OK",
                            reason="Unchecked: Initial alarm creation")
        self.assertEqual(self.h.run(event)["sent"], [])

    def test_data_gap_and_restore(self):
        self.h.run(alarm_event("OK", T1, previous="INSUFFICIENT_DATA", failed=0))
        self.assertEqual(self.h.run(alarm_event("INSUFFICIENT_DATA", T2))["sent"], ["insufficient"])
        self.assertIn("WARN — Availability Burn Rate: DATA INSUFFICIENT", self.h.sent[0])
        self.assertEqual(self.h.run(alarm_event("OK", T3, previous="INSUFFICIENT_DATA",
                                                failed=0))["sent"], ["data_restored"])
        self.assertIn("INFO — Availability Burn Rate: DATA RESTORED", self.h.sent[1])

    def test_alarm_after_normalized_alarms_again(self):
        self.h.run(alarm_event("ALARM", T1, failed=9))
        self.h.run(alarm_event("OK", T2, previous="ALARM", failed=8))
        self.assertEqual(self.h.run(alarm_event("ALARM", T3, failed=10))["sent"], ["alarm"])
        self.assertEqual(len(self.h.sent), 3)

    def test_alarm_message_has_no_reason_data_numbers(self):
        self.h.run(alarm_event("ALARM", T1, failed=9))
        self.assertNotIn("Observed", self.h.sent[0])


class DeliveryFailureTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        quiet = patch.object(br.LOG, "disabled", True)
        quiet.start()
        self.addCleanup(quiet.stop)

    def test_failure_propagates_and_is_not_marked(self):
        self.h.fail = 1
        with patch.object(br.LOG, "disabled", False), \
                self.assertLogs(br.LOG, "ERROR") as logs, self.assertRaises(OSError):
            self.h.run(alarm_event("ALARM", T1, failed=9))
        self.assertIn("notify_failed", logs.output[0])
        row = self.h.row()
        self.assertEqual(row["alarm_state"], "ALARM")
        self.assertNotIn("notified_state", row)
        self.assertNotIn("lease_until", row)  # released for the retry

    def test_lambda_retry_delivers_once(self):
        event = alarm_event("ALARM", T1, failed=9)
        self.h.fail = 2
        for _ in range(2):  # first attempt and first async retry fail
            with self.assertRaises(OSError):
                self.h.run(event)
            self.h.clock += timedelta(minutes=1)
        self.assertEqual(self.h.run(event)["sent"], ["alarm"])
        self.assertEqual(self.h.run(event)["sent"], [])
        self.assertEqual(len(self.h.sent), 1)
        self.assertEqual(self.h.row()["notified_state"], "ALARM")

    def test_all_retries_fail_then_ok_sends_nothing(self):
        event = alarm_event("ALARM", T1, failed=9)
        self.h.fail = 3
        for _ in range(3):
            with self.assertRaises(OSError):
                self.h.run(event)
        self.h.clock += timedelta(minutes=56)
        # The ALARM was never delivered, so there is nothing to normalize.
        self.assertEqual(self.h.run(alarm_event("OK", T2, previous="ALARM", failed=8))["sent"], [])
        self.assertEqual(self.h.sent, [])

    def test_failed_ok_notification_is_retried_by_redelivery(self):
        self.h.run(alarm_event("ALARM", T1, failed=9))
        ok = alarm_event("OK", T2, previous="ALARM", failed=8)
        self.h.fail = 1
        with self.assertRaises(OSError):
            self.h.run(ok)
        self.assertEqual(self.h.run(ok)["sent"], ["normalized"])

    def test_active_lease_blocks_concurrent_sender(self):
        self.h.run(alarm_event("OK", T1, previous="INSUFFICIENT_DATA", failed=0))
        alarm = alarm_event("ALARM", T2, failed=9)
        self.h.table.update_item(
            Key={"instance_id": KEY[0], "event_time": KEY[1]},
            UpdateExpression="SET lease_at = :at, lease_until = :until",
            ExpressionAttributeValues={":at": br.state_time(T2),
                                       ":until": int(self.h.clock.timestamp()) + 60})
        with self.assertRaises(br.NotificationInFlight):
            self.h.run(alarm)
        self.assertEqual(self.h.sent, [])
        # After the lease expires (sender crashed), a retry takes over.
        self.h.clock += timedelta(seconds=br.LEASE_SECONDS + 1)
        self.assertEqual(self.h.run(alarm)["sent"], ["alarm"])

    def test_reader_with_old_snapshot_does_not_resend(self):
        # B reads the row, A then sends and marks, then B tries to claim.
        self.h.delivery = "disabled"
        self.h.run(alarm_event("ALARM", T1, failed=9))
        key = br.row_key(NAME)
        snapshot = br.current(self.h.table, key)
        self.h.delivery = "enabled"
        self.h.run(alarm_event("ALARM", T1, failed=9))  # A
        self.assertEqual(len(self.h.sent), 1)
        reads = iter([snapshot])
        real = br.current
        with patch.object(br, "current", side_effect=lambda t, k: next(reads, None) or real(t, k)):
            sent = br.reconcile(self.h.table, key, notify=self.h.notify,
                                now=int(self.h.clock.timestamp()), deliver=True, alarm_name=NAME)
        self.assertEqual(sent, [])
        self.assertEqual(len(self.h.sent), 1)

    def test_state_change_during_send_is_reconciled(self):
        # While the ALARM message is in flight, the OK event is processed by a
        # second invocation. It must not conclude "nothing owed".
        inner = {}

        def deliver_ok():
            try:
                self.h.run(alarm_event("OK", T2, previous="ALARM", failed=8))
            except br.NotificationInFlight as exc:
                inner["error"] = exc

        self.h.during_notify = deliver_ok
        result = self.h.run(alarm_event("ALARM", T1, failed=9))
        self.assertIsInstance(inner.get("error"), br.NotificationInFlight)
        self.assertEqual(result["sent"], ["alarm", "normalized"])
        self.assertEqual(self.h.row()["notified_state"], "OK")
        # The second invocation's Lambda retry then finds nothing owed.
        self.assertEqual(self.h.run(alarm_event("OK", T2, previous="ALARM", failed=8))["sent"], [])
        self.assertEqual(len(self.h.sent), 2)


class ShadowTests(unittest.TestCase):
    def test_shadow_records_state_but_sends_nothing(self):
        h = Harness(delivery="disabled")
        result = h.run(alarm_event("ALARM", T1, failed=9))
        self.assertEqual(result["sent"], ["shadow:alarm"])
        self.assertEqual(h.sent, [])
        self.assertEqual(h.row()["alarm_state"], "ALARM")
        self.assertNotIn("notified_state", h.row())
        self.assertNotIn("lease_until", h.row())
        self.assertEqual((h.row()["shadow_notified_state"], h.row()["shadow_notified_at"]),
                         ("ALARM", br.state_time(T1)))

    def test_switch_to_live_during_alarm(self):
        h = Harness(delivery="disabled")
        h.run(alarm_event("ALARM", T1, failed=9))
        h.delivery = "enabled"
        # Nothing was announced in shadow mode, so OK sends nothing.
        self.assertEqual(h.run(alarm_event("OK", T2, previous="ALARM", failed=8))["sent"], [])
        self.assertEqual(h.run(alarm_event("ALARM", T3, failed=9))["sent"], ["alarm"])

    def test_only_enabled_sends(self):
        for value in ("", "true", "ENABLED", "live", "disabled"):
            h = Harness(delivery=value)
            h.run(alarm_event("ALARM", T1, failed=9))
            self.assertEqual(h.sent, [], value)


T4 = "2026-11-24T06:10:00.000+0000"
LIVE_FIELDS = ("notified_state", "notified_at", "lease_at", "lease_until")


class ShadowLifecycleTests(unittest.TestCase):
    """Shadow logs what live would send, with its own ledger (shadow_notified_*)."""

    def setUp(self):
        self.h = Harness(delivery="disabled")

    def run_logged(self, event):
        with self.assertLogs(br.LOG, "INFO") as logs:
            result = self.h.run(event)
        records = [json.loads(r.getMessage()) for r in logs.records
                   if r.getMessage().startswith('{"burn_rate": "shadow"')]
        return result, records

    def assert_no_live_fields(self):
        row = self.h.row()
        self.assertFalse([f for f in LIVE_FIELDS if f in row], row)
        self.assertEqual(self.h.sent, [])

    def test_alarm_then_normalized(self):
        result, (alarm,) = self.run_logged(alarm_event("ALARM", T1))
        self.assertEqual(result["sent"], ["shadow:alarm"])
        self.assertTrue(alarm["message"].startswith("HIGH — Availability Burn Rate: ALARM\n"))
        result, (normalized,) = self.run_logged(alarm_event("OK", T2, previous="ALARM"))
        self.assertEqual(result["sent"], ["shadow:normalized"])
        self.assertEqual((normalized["would_send"], normalized["delivered"]), ("normalized", False))
        self.assertTrue(normalized["message"].startswith(
            "HIGH — Availability Burn Rate: NORMALIZED (alarm back to OK)\n"))
        self.assertIn("Alarm: ALARM -> OK", normalized["message"])
        self.assertEqual(self.h.row()["shadow_notified_state"], "OK")
        self.assert_no_live_fields()

    def test_data_gap_and_restore(self):
        self.h.run(alarm_event("OK", T1, previous="INSUFFICIENT_DATA"))  # initial: silent
        self.assertNotIn("shadow_notified_state", self.h.row())
        result, (gap,) = self.run_logged(alarm_event("INSUFFICIENT_DATA", T2))
        self.assertEqual(result["sent"], ["shadow:insufficient"])
        self.assertTrue(gap["message"].startswith("WARN — Availability Burn Rate: DATA INSUFFICIENT\n"))
        result, (restored,) = self.run_logged(alarm_event("OK", T3, previous="INSUFFICIENT_DATA"))
        self.assertEqual(result["sent"], ["shadow:data_restored"])
        self.assertTrue(restored["message"].startswith("INFO — Availability Burn Rate: DATA RESTORED\n"))
        self.assert_no_live_fields()

    def test_alarm_to_insufficient_is_not_a_recovery(self):
        self.h.run(alarm_event("ALARM", T1))
        result, (gap,) = self.run_logged(alarm_event("INSUFFICIENT_DATA", T2, previous="ALARM"))
        self.assertEqual(result["sent"], ["shadow:insufficient"])
        self.assertNotIn("NORMALIZED", gap["message"])
        self.assertIn("not proof of downtime or recovery", gap["message"])
        _, (restored,) = self.run_logged(alarm_event("OK", T3, previous="INSUFFICIENT_DATA"))
        self.assertEqual(restored["would_send"], "data_restored")
        self.assertNotIn("NORMALIZED", restored["message"])

    def test_same_sequence_as_live(self):
        events = [alarm_event("OK", T1, previous="INSUFFICIENT_DATA"),
                  alarm_event("ALARM", T2), alarm_event("ALARM", T2),
                  alarm_event("OK", T3, previous="ALARM"),
                  alarm_event("ALARM", T2),  # stale
                  alarm_event("INSUFFICIENT_DATA", T4),
                  alarm_event("OK", "2026-11-24T08:00:00.000+0000", previous="INSUFFICIENT_DATA")]
        live, shadow = Harness(), Harness(delivery="disabled")
        live_kinds = [k for e in events for k in live.run(e)["sent"]]
        shadow_kinds = [k for e in events for k in shadow.run(e)["sent"]]
        self.assertEqual(live_kinds, ["alarm", "normalized", "insufficient", "data_restored"])
        self.assertEqual(shadow_kinds, ["shadow:" + k for k in live_kinds])
        self.assertEqual(shadow.sent, [])

    def test_duplicates_and_stale_events_log_once(self):
        self.h.run(alarm_event("ALARM", T1))
        for event in (alarm_event("ALARM", T1), alarm_event("ALARM", T1, event_id="other-id")):
            self.assertEqual(self.h.run(event)["sent"], [])
        self.h.run(alarm_event("OK", T2, previous="ALARM"))
        self.assertEqual(self.h.run(alarm_event("ALARM", T1))["sent"], [])  # older than OK
        self.assertEqual(self.h.row()["alarm_state"], "OK")

    def test_out_of_order_ok_first(self):
        self.assertEqual(self.h.run(alarm_event("OK", T2, previous="ALARM"))["sent"], [])
        self.assertEqual(self.h.run(alarm_event("ALARM", T1))["sent"], [])

    def test_unchecked_insufficient_is_silent(self):
        event = alarm_event("INSUFFICIENT_DATA", T1, reason="Unchecked: Initial alarm creation")
        self.assertEqual(self.h.run(event)["sent"], [])

    def test_concurrent_shadow_mark_logs_once(self):
        self.h.run(alarm_event("ALARM", T1))
        row = self.h.table.items[KEY]
        del row["shadow_notified_state"], row["shadow_notified_at"]
        # Another invocation marks between this one's read and its mark.
        original = br.shadow_mark

        def racing(table, key, at, state):
            original(table, key, at, state)
            return original(table, key, at, state)
        with patch.object(br, "shadow_mark", side_effect=racing):
            self.assertEqual(self.h.run(alarm_event("ALARM", T1))["sent"], [])

    def test_state_change_after_read_is_not_marked_on_the_new_state(self):
        self.h.run(alarm_event("ALARM", T1))
        row = self.h.table.items[KEY]
        stale = dict(row)
        del stale["shadow_notified_state"], stale["shadow_notified_at"]
        del row["shadow_notified_state"], row["shadow_notified_at"]
        # Another invocation accepted OK after this one read the ALARM row.
        row.update(alarm_state="OK", previous_state="ALARM", state_at=br.state_time(T2))
        snapshots = [stale]
        original = br.current
        with patch.object(br, "current",
                          side_effect=lambda table, key: snapshots.pop(0) if snapshots
                          else original(table, key)):
            sent = br.reconcile(self.h.table, br.row_key(NAME), notify=self.h.notify,
                                now=int(self.h.clock.timestamp()), deliver=False,
                                alarm_name=NAME)
        self.assertEqual(sent, [])  # nothing owed to an OK the shadow never saw ALARM for
        self.assertNotIn("shadow_notified_at", self.h.row())

    def test_live_ignores_the_shadow_ledger(self):
        self.h.run(alarm_event("ALARM", T1))
        self.h.run(alarm_event("OK", T2, previous="ALARM"))
        self.h.delivery = "enabled"
        # Shadow logged ALARM and NORMALIZED; live still announces the next episode.
        self.assertEqual(self.h.run(alarm_event("ALARM", T3))["sent"], ["alarm"])
        self.assertEqual(self.h.run(alarm_event("OK", T4, previous="ALARM"))["sent"], ["normalized"])
        row = self.h.row()
        self.assertEqual(row["notified_state"], "OK")
        self.assertEqual(row["shadow_notified_state"], "OK")  # untouched since T2
        self.assertEqual(row["shadow_notified_at"], br.state_time(T2))

    def test_shadow_alarm_does_not_block_live_alarm_redelivery(self):
        self.h.run(alarm_event("ALARM", T1))
        self.h.delivery = "enabled"
        # The same ALARM redelivered after the switch is still owed to live.
        self.assertEqual(self.h.run(alarm_event("ALARM", T1))["sent"], ["alarm"])

    def test_live_never_writes_the_shadow_ledger(self):
        live = Harness()
        for event in (alarm_event("ALARM", T1), alarm_event("OK", T2, previous="ALARM")):
            live.run(event)
        self.assertFalse([k for k in live.row() if k.startswith("shadow")])

    def test_shadow_writes_two_updates_per_logged_state(self):
        self.h.run(alarm_event("ALARM", T1))
        writes = [c for c in self.h.table.calls if c[0] == "update_item"]
        self.assertEqual(len(writes), 2)  # accept, shadow mark


class IsolationTests(unittest.TestCase):
    def test_uses_only_its_own_row(self):
        h = Harness()
        h.table.items[("http#sunlightjetrans.com", "~STATE")] = {
            "instance_id": "http#sunlightjetrans.com", "event_time": "~STATE",
            "http_status": "down"}
        h.run(alarm_event("ALARM", T1, failed=9))
        h.run(alarm_event("OK", T2, previous="ALARM", failed=8))
        self.assertEqual({pk for pk, _ in h.table.items}, {"http#sunlightjetrans.com", KEY[0]})
        self.assertEqual(h.table.items[("http#sunlightjetrans.com", "~STATE")]["http_status"], "down")
        self.assertTrue(all(call[0] in ("get_item", "update_item") for call in h.table.calls))

    def test_writes_only_on_events(self):
        h = Harness()
        h.run(alarm_event("ALARM", T1, failed=9))
        writes = [c for c in h.table.calls if c[0] == "update_item"]
        self.assertEqual(len(writes), 3)  # accept, claim, mark


class HandlerRoutingTests(unittest.TestCase):
    """handler.handler dispatches aws.cloudwatch events; other sources are unchanged."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("burn_rate_handler", ROOT / "lambda" / "handler.py")
        self.handler = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.handler)
        self.table = FakeTable()
        self.sent = []
        env = {"TABLE_NAME": "test-only", "APP_INSTANCE_ID": "i-app", "OBS_INSTANCE_ID": "i-obs",
               "DISCORD_CLOUDFRONT_URL": "https://example.cloudfront.net/notify",
               "DISCORD_TOKEN_PARAMETER": "/test/relay-token",
               "BURN_RATE_ALARM_NAME": NAME, "BURN_RATE_ALARM_ARN": ARN,
               "BURN_RATE_DELIVERY": "enabled"}
        for p in (patch.dict(os.environ, env),
                  patch("boto3.client", side_effect=AssertionError("real AWS client")),
                  patch("boto3.resource", side_effect=AssertionError("real AWS resource")),
                  patch.object(self.handler, "table", return_value=self.table),
                  patch.object(self.handler, "notify", side_effect=self.sent.append),
                  patch.object(self.handler, "metric", side_effect=AssertionError("metric")),
                  patch.object(self.handler, "probe_http", side_effect=AssertionError("probe")),
                  patch.object(self.handler, "burn_rate_context", side_effect=self.fake_context)):
            p.start()
            self.addCleanup(p.stop)
        self.contexts = []

    def fake_context(self, context=None):
        handler_self = self

        class Fixed:
            calls = 0

            def lines(self, state_at):
                Fixed.calls += 1
                return ["Now: UP (last check 09:08 JST)", "Last 60 completed min: stub",
                        "Pattern: stub"]
        handler_self.contexts.append((context, Fixed))
        return Fixed()

    def test_context_is_wired_into_the_message(self):
        self.handler.handler(alarm_event("ALARM", T1, failed=9))
        self.assertIn("Now: UP (last check 09:08 JST)\nLast 60 completed min: stub", self.sent[0])

    def test_no_preview_route(self):
        # Removed on 2026-11-28: production context is checked in shadow.
        self.assertNotIn("preview", (ROOT / "lambda" / "handler.py").read_text())
        self.assertFalse(hasattr(br, "preview"))

    def test_real_context_uses_lambda_time_left(self):
        class LambdaContext:
            def get_remaining_time_in_millis(self):
                return 40_000
        spec = importlib.util.spec_from_file_location("burn_rate_handler_real",
                                                      ROOT / "lambda" / "handler.py")
        real = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(real)
        ctx = real.burn_rate_context(LambdaContext())
        # 40 s left: lookups must end 25 s before the Lambda timeout.
        self.assertAlmostEqual(ctx.remaining(), 15.0, delta=0.5)
        self.assertAlmostEqual(real.burn_rate_context(None).remaining(), 30.0, delta=0.5)

    def test_alarm_event_routed(self):
        result = self.handler.handler(alarm_event("ALARM", T1, failed=9))
        self.assertEqual(result["sent"], ["alarm"])
        self.assertEqual(len(self.sent), 1)

    def test_delivery_defaults_to_disabled(self):
        with patch.dict(os.environ, {}, clear=False):
            del os.environ["BURN_RATE_DELIVERY"]
            result = self.handler.handler(alarm_event("ALARM", T1, failed=9))
        self.assertEqual(result["sent"], ["shadow:alarm"])
        self.assertEqual(self.sent, [])

    def test_missing_alarm_configuration_ignores(self):
        with patch.dict(os.environ, {"BURN_RATE_ALARM_NAME": "", "BURN_RATE_ALARM_ARN": ""}):
            self.assertEqual(self.handler.handler(alarm_event("ALARM", T1, failed=9)),
                             {"ignored": "unmonitored alarm"})
        self.assertEqual(self.table.items, {})

    def test_other_cloudwatch_events_ignored(self):
        event = alarm_event("ALARM", T1)
        event["detail-type"] = "CloudWatch Alarm Configuration Change"
        self.assertEqual(self.handler.handler(event), {"ignored": "unsupported event"})

    def test_unknown_source_still_ignored(self):
        self.assertEqual(self.handler.handler({"source": "aws.s3"}), {"ignored": "unsupported source"})


def tf(name):
    return (ROOT / name).read_text()


def alarm_block():
    return re.search(r'resource "aws_cloudwatch_metric_alarm" "burn_rate_fast" \{(.*?)\n\}',
                     tf("burn_rate.tf"), re.S).group(1)


def tf_value(block, attribute):
    return re.search(rf"^\s*{attribute}\s*=\s*(.+?)\s*$", block, re.M).group(1).strip('"')


class TerraformConsistencyTests(unittest.TestCase):
    def test_alarm_matches_specification_and_messages(self):
        block = alarm_block()
        expected = {"namespace": "GMS/Availability", "metric_name": "HTTPUp",
                    "statistic": "Minimum", "period": "60",
                    "evaluation_periods": str(br.EVALUATION_PERIODS),
                    "datapoints_to_alarm": str(br.DATAPOINTS_TO_ALARM), "threshold": "1",
                    "comparison_operator": "LessThanThreshold", "treat_missing_data": "missing"}
        for attribute, value in expected.items():
            self.assertEqual(tf_value(block, attribute), value, attribute)
        self.assertIn('dimensions        = { Target = "sunlightjetrans.com" }', block)
        self.assertIn(f'burn_rate_alarm_name = "{NAME}"', tf("burn_rate.tf"))
        self.assertNotRegex(block, r"(?m)^\s*unit\s*=")
        for actions in ("alarm_actions", "ok_actions", "insufficient_data_actions"):
            self.assertEqual(tf_value(block, actions), "[]")

    def test_threshold_arithmetic(self):
        # Design rationale only (Runbook §13.1); messages no longer show it.
        slo, design_rate = 0.99, 14.4
        self.assertAlmostEqual(7 * 24 * 60 * (1 - slo), 100.8)
        rate = lambda failed: failed / br.EVALUATION_PERIODS / (1 - slo)  # noqa: E731
        self.assertGreater(rate(br.DATAPOINTS_TO_ALARM), design_rate)
        self.assertLessEqual(rate(br.DATAPOINTS_TO_ALARM - 1), design_rate)
        self.assertIn("9 failed samples = 15x burn rate", tf("burn_rate.tf"))

    def test_rule_matches_only_this_alarm_and_is_gated(self):
        text = tf("burn_rate.tf")
        rule = re.search(r'resource "aws_cloudwatch_event_rule" "burn_rate" \{(.*?)\n\}', text, re.S).group(1)
        self.assertIn('source        = ["aws.cloudwatch"]', rule)
        self.assertIn('"detail-type" = ["CloudWatch Alarm State Change"]', rule)
        self.assertIn("resources     = [aws_cloudwatch_metric_alarm.burn_rate_fast.arn]", rule)
        self.assertIn("alarmName = [aws_cloudwatch_metric_alarm.burn_rate_fast.alarm_name]", rule)
        self.assertIn('state = var.burn_rate_alarm_rollout == "off" ? "DISABLED" : "ENABLED"', rule)
        main = tf("main.tf")
        self.assertIn('BURN_RATE_DELIVERY   = var.burn_rate_alarm_rollout == "live" ? "enabled" : "disabled"', main)
        variables = tf("variables.tf")
        rollout = re.search(r'variable "burn_rate_alarm_rollout" \{(.*?)\n\}', variables, re.S).group(1)
        self.assertIn('default = "off"', rollout)

    def test_lease_covers_lambda_timeout(self):
        # A lease shorter than the timeout lets a retry take over from a
        # sender that is still running (duplicate message). A lease longer
        # than the second async retry (about 3 min) would block takeover after
        # a crash until all retries are used.
        function = re.search(r'resource "aws_lambda_function" "availability" \{(.*?)\n\}',
                             tf("main.tf"), re.S).group(1)
        timeout = int(tf_value(function, "timeout"))
        self.assertGreaterEqual(br.LEASE_SECONDS, timeout)
        self.assertLess(br.LEASE_SECONDS, 180)

    def test_no_new_execution_role_permissions(self):
        self.assertNotIn("aws_iam", tf("burn_rate.tf"))


def m_of_n(samples, m, n):
    """Complete-data model of M-of-N: True when >= m of the last n samples breach."""
    states = []
    for end in range(n, len(samples) + 1):
        window = samples[end - n:end]
        states.append(sum(1 for value in window if value < 1) >= m)
    return states


class EvaluationModelTests(unittest.TestCase):
    """Model only. CloudWatch's own evaluation needs the integration test (Runbook §13.6)."""

    def setUp(self):
        block = alarm_block()
        self.m = int(tf_value(block, "datapoints_to_alarm"))
        self.n = int(tf_value(block, "evaluation_periods"))

    def test_eight_failures_in_full_window_stays_ok(self):
        samples = [1] * 52 + [0] * 8
        self.assertEqual(m_of_n(samples, self.m, self.n), [False])

    def test_nine_failures_in_full_window_alarms(self):
        samples = [0, 1] * 9 + [1] * 42
        self.assertEqual(m_of_n(samples, self.m, self.n), [True])

    def test_isolated_two_minute_interruption_does_not_alarm(self):
        samples = [1] * 120 + [0, 0] + [1] * 120
        self.assertFalse(any(m_of_n(samples, self.m, self.n)))

    def test_alarm_clears_when_oldest_failure_leaves_window(self):
        samples = [1] * 60 + [0] * 9 + [1] * 60
        states = m_of_n(samples, self.m, self.n)
        first = states.index(True)
        self.assertEqual(first + self.n - 1, 68)  # window ends at the ninth failure
        # ALARM while all nine failures (minutes 60-68) are inside the window:
        # windows ending at 68..119, i.e. 52 evaluations.
        self.assertTrue(all(states[first:first + 52]))
        self.assertFalse(states[first + 52])  # the first failure left the window
        self.assertFalse(any(states[first + 52:]))


if __name__ == "__main__":
    unittest.main()
