"""Continuous failure (6 of 6) alarm notifications, next to the 9-of-60 alarm. No AWS calls.

The two alarms share the state logic of burn_rate.py but have separate
profiles, state rows, leases, live and shadow ledgers, rules and delivery
variables. CloudWatch's own evaluation of the alarm is measured by the
isolated integration test (Availability Runbook §14), not here.
"""
import os as _os, sys as _sys  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import no_network  # noqa: E402,F401  Blocks real network access (no AWS calls).
import importlib.util  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "lambda"))

import burn_rate as br  # noqa: E402
from fake_dynamodb import FakeTable  # noqa: E402
from test_burn_rate import ARN, BASE, NAME, T1, T2, T3, alarm_event  # noqa: E402

CF_NAME = "gmsv5-availability-continuous-failure"
CF_ARN = "arn:aws:cloudwatch:ap-northeast-1:000000000000:alarm:" + CF_NAME
CF_KEY = ("contfail#" + CF_NAME, "~STATE")
BR_KEY = ("burnrate#" + NAME, "~STATE")
FIXTURES = ROOT / "tests" / "fixtures"


def cf_event(value, ts, previous="OK", **kwargs):
    return alarm_event(value, ts, previous=previous, name=CF_NAME, arn=CF_ARN, **kwargs)


class Two:
    """Both alarms on one table, each with its own delivery mode."""

    def __init__(self, br_delivery="enabled", cf_delivery="enabled"):
        self.table, self.sent, self.clock = FakeTable(), [], BASE
        self.delivery = {"br": br_delivery, "cf": cf_delivery}

    def notify(self, text):
        self.sent.append(text)

    def run(self, which, event):
        name, arn, profile = ((NAME, ARN, br.BURN_RATE) if which == "br"
                              else (CF_NAME, CF_ARN, br.CONTINUOUS_FAILURE))
        return br.handle(event, table=self.table, notify=self.notify, now=lambda: self.clock,
                         alarm_name=name, alarm_arn=arn, delivery=self.delivery[which],
                         profile=profile)

    def row(self, key):
        return self.table.row(*key) or {}


class ProfileTests(unittest.TestCase):
    ITEM = {"alarm_state": "ALARM", "previous_state": "OK", "state_at": "2026-11-24T00:06:40.989Z"}

    def test_burn_rate_text_is_unchanged(self):
        # The 9-of-60 messages must stay exactly as applied on 2026-11-28.
        self.assertEqual(br.HEADERS, {
            "alarm": "HIGH — Availability Burn Rate: ALARM",
            "normalized": "HIGH — Availability Burn Rate: NORMALIZED (alarm back to OK)",
            "insufficient": "WARN — Availability Burn Rate: DATA INSUFFICIENT",
            "data_restored": "INFO — Availability Burn Rate: DATA RESTORED"})
        text = br.message("alarm", self.ITEM, NAME)
        self.assertEqual(text, br.message("alarm", self.ITEM, NAME, None, br.BURN_RATE))
        self.assertIn("\nAlarm threshold: 9/60 failed datapoints\n", text)
        self.assertEqual(br.row_key(NAME), {"instance_id": "burnrate#" + NAME, "event_time": "~STATE"})

    def test_continuous_failure_text(self):
        p = br.CONTINUOUS_FAILURE
        heads = {k: p.header(k) for k in br.KINDS}
        self.assertEqual(heads["alarm"], "HIGH — Availability Continuous Failure: ALARM")
        self.assertEqual(heads["normalized"],
                         "HIGH — Availability Continuous Failure: NORMALIZED (alarm back to OK)")
        self.assertEqual(len(set(heads.values())), 4)
        text = br.message("alarm", self.ITEM, CF_NAME, None, p)
        lines = text.split("\n")
        self.assertEqual(lines[1], p.threshold)
        self.assertEqual(p.threshold, "Alarm threshold: 6/6 failed datapoints in a row "
                                      "(the most recent ones; missing minutes are skipped)")
        self.assertNotIn("Burn Rate", text)
        self.assertNotIn("9/60", text)
        self.assertLessEqual(len(text), 1800)

    def test_normalized_is_never_a_recovery_claim(self):
        text = br.message("normalized", dict(self.ITEM, alarm_state="OK", previous_state="ALARM"),
                          CF_NAME, None, br.CONTINUOUS_FAILURE)
        self.assertIn("a passed check is among the 6 most recent datapoints", text)
        self.assertIn("NOT the confirmed recovery time", text)
        self.assertIn("does not confirm the site is up now", text)
        self.assertNotIn("RECOVERED", text.replace("HTTP DOWN/RECOVERED", ""))

    def test_insufficient_is_not_a_recovery(self):
        for kind in ("insufficient", "data_restored"):
            text = br.message(kind, dict(self.ITEM, alarm_state="OK"), CF_NAME, None,
                              br.CONTINUOUS_FAILURE).lower()
            self.assertNotIn("recovered", text)
            self.assertNotIn("normalized", text)

    def test_rows_are_separate(self):
        self.assertEqual(br.row_key(CF_NAME, br.CONTINUOUS_FAILURE)["instance_id"],
                         "contfail#" + CF_NAME)


class IndependenceTests(unittest.TestCase):
    def test_both_alarms_send_independently(self):
        two = Two()
        self.assertEqual(two.run("cf", cf_event("ALARM", T1))["sent"], ["alarm"])
        self.assertEqual(two.run("br", alarm_event("ALARM", T2))["sent"], ["alarm"])
        self.assertEqual(two.run("cf", cf_event("OK", T2, previous="ALARM"))["sent"], ["normalized"])
        self.assertEqual(two.run("br", alarm_event("OK", T3, previous="ALARM"))["sent"],
                         ["normalized"])
        heads = [t.split("\n")[0] for t in two.sent]
        self.assertEqual(heads, [
            "HIGH — Availability Continuous Failure: ALARM",
            "HIGH — Availability Burn Rate: ALARM",
            "HIGH — Availability Continuous Failure: NORMALIZED (alarm back to OK)",
            "HIGH — Availability Burn Rate: NORMALIZED (alarm back to OK)"])
        self.assertEqual(set(two.table.items), {BR_KEY, CF_KEY})

    def test_ordering_and_duplicates_are_per_alarm(self):
        two = Two()
        two.run("cf", cf_event("OK", T3, previous="ALARM"))
        # An older ALARM of one alarm is stale; the other alarm's newer state is irrelevant.
        self.assertEqual(two.run("cf", cf_event("ALARM", T1))["burn_rate"], "stale")
        self.assertEqual(two.run("br", alarm_event("ALARM", T1))["burn_rate"], "accepted")
        self.assertEqual(two.run("br", alarm_event("ALARM", T1))["burn_rate"], "duplicate")
        self.assertEqual(len(two.sent), 1)

    def test_lease_of_one_does_not_block_the_other(self):
        two = Two()
        two.run("br", alarm_event("ALARM", T1))
        row = two.table.items[BR_KEY]
        row.update(notified_state="OK", lease_at=row["state_at"],
                   lease_until=int(two.clock.timestamp()) + 60)
        with self.assertRaises(br.NotificationInFlight):
            two.run("br", alarm_event("ALARM", T1))
        self.assertEqual(two.run("cf", cf_event("ALARM", T1))["sent"], ["alarm"])

    def test_delivery_modes_and_ledgers_are_independent(self):
        two = Two(br_delivery="enabled", cf_delivery="disabled")
        self.assertEqual(two.run("cf", cf_event("ALARM", T1))["sent"], ["shadow:alarm"])
        self.assertEqual(two.run("br", alarm_event("ALARM", T1))["sent"], ["alarm"])
        cf, brow = two.row(CF_KEY), two.row(BR_KEY)
        self.assertEqual((cf.get("shadow_notified_state"), cf.get("notified_state")), ("ALARM", None))
        self.assertEqual((brow.get("notified_state"), brow.get("shadow_notified_state")), ("ALARM", None))
        self.assertEqual(len(two.sent), 1)
        # Switching the continuous alarm to live does not replay its shadow history.
        two.delivery["cf"] = "enabled"
        self.assertEqual(two.run("cf", cf_event("OK", T2, previous="ALARM"))["sent"], [])
        self.assertEqual(two.run("cf", cf_event("ALARM", T3))["sent"], ["alarm"])

    def test_shadow_lifecycle_for_the_continuous_alarm(self):
        two = Two(cf_delivery="disabled")
        kinds = []
        for event in (cf_event("OK", T1, previous="INSUFFICIENT_DATA"), cf_event("ALARM", T2),
                      cf_event("ALARM", T2), cf_event("OK", T3, previous="ALARM")):
            kinds += two.run("cf", event)["sent"]
        self.assertEqual(kinds, ["shadow:alarm", "shadow:normalized"])
        self.assertEqual(two.sent, [])

    def test_shadow_log_uses_the_continuous_failure_text(self):
        two = Two(cf_delivery="disabled")
        with self.assertLogs(br.LOG, "INFO") as logs:
            two.run("cf", cf_event("ALARM", T1))
        (record,) = [json.loads(r.getMessage()) for r in logs.records
                     if r.getMessage().startswith('{"burn_rate": "shadow"')]
        self.assertEqual(record["alarm"], "continuous_failure")
        self.assertTrue(record["message"].startswith("HIGH — Availability Continuous Failure: ALARM\n"))
        self.assertIn("6/6 failed datapoints in a row", record["message"])


class RejectionTests(unittest.TestCase):
    def test_name_and_arn_must_match_the_same_alarm(self):
        two = Two()
        crossed = [alarm_event("ALARM", T1, name=CF_NAME, arn=ARN),
                   alarm_event("ALARM", T1, name=NAME, arn=CF_ARN)]
        self.assertEqual(two.run("cf", crossed[0]), {"ignored": "unmonitored alarm ARN"})
        self.assertEqual(two.run("br", crossed[1]), {"ignored": "unmonitored alarm ARN"})
        self.assertEqual(two.run("br", crossed[0]), {"ignored": "unmonitored alarm"})
        self.assertEqual(two.table.items, {})


class HandlerDispatchTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("contfail_handler", ROOT / "lambda" / "handler.py")
        self.handler = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.handler)
        self.table, self.sent = FakeTable(), []
        self.env = {"TABLE_NAME": "test-only", "APP_INSTANCE_ID": "i-app", "OBS_INSTANCE_ID": "i-obs",
                    "DISCORD_CLOUDFRONT_URL": "https://example.cloudfront.net/notify",
                    "DISCORD_TOKEN_PARAMETER": "/test/relay-token",
                    "BURN_RATE_ALARM_NAME": NAME, "BURN_RATE_ALARM_ARN": ARN,
                    "BURN_RATE_DELIVERY": "enabled",
                    "CONTINUOUS_FAILURE_ALARM_NAME": CF_NAME, "CONTINUOUS_FAILURE_ALARM_ARN": CF_ARN,
                    "CONTINUOUS_FAILURE_DELIVERY": "disabled"}
        for p in (patch.dict(os.environ, self.env),
                  patch("boto3.client", side_effect=AssertionError("real AWS client")),
                  patch("boto3.resource", side_effect=AssertionError("real AWS resource")),
                  patch.object(self.handler, "table", return_value=self.table),
                  patch.object(self.handler, "notify", side_effect=self.sent.append),
                  patch.object(self.handler, "burn_rate_context", return_value=None)):
            p.start()
            self.addCleanup(p.stop)

    def test_each_alarm_uses_its_own_profile_and_delivery(self):
        self.assertEqual(self.handler.handler(cf_event("ALARM", T1))["sent"], ["shadow:alarm"])
        self.assertEqual(self.handler.handler(alarm_event("ALARM", T1))["sent"], ["alarm"])
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0].startswith("HIGH — Availability Burn Rate: ALARM"))
        self.assertEqual(set(self.table.items), {BR_KEY, CF_KEY})

    def test_continuous_delivery_defaults_to_disabled(self):
        del os.environ["CONTINUOUS_FAILURE_DELIVERY"]
        self.assertEqual(self.handler.handler(cf_event("ALARM", T1))["sent"], ["shadow:alarm"])

    def test_unconfigured_continuous_alarm_is_rejected(self):
        os.environ["CONTINUOUS_FAILURE_ALARM_NAME"] = ""
        os.environ["CONTINUOUS_FAILURE_ALARM_ARN"] = ""
        self.assertEqual(self.handler.handler(cf_event("ALARM", T1)), {"ignored": "unmonitored alarm"})
        self.assertEqual(self.table.items, {})

    def test_crossed_name_and_arn_rejected_through_the_handler(self):
        self.assertEqual(self.handler.handler(alarm_event("ALARM", T1, name=CF_NAME, arn=ARN)),
                         {"ignored": "unmonitored alarm ARN"})
        self.assertEqual(self.handler.handler(alarm_event("ALARM", T1, name="other", arn=CF_ARN)),
                         {"ignored": "unmonitored alarm"})
        self.assertEqual(self.table.items, {})

    def test_burn_rate_only_environment_still_works(self):
        for key in ("CONTINUOUS_FAILURE_ALARM_NAME", "CONTINUOUS_FAILURE_ALARM_ARN",
                    "CONTINUOUS_FAILURE_DELIVERY"):
            del os.environ[key]
        self.assertEqual(self.handler.handler(alarm_event("ALARM", T1))["sent"], ["alarm"])


def fixture(name):
    return json.loads((FIXTURES / "continuous-failure-events.json").read_text())["events"][name]


class RealEventTests(unittest.TestCase):
    """Events captured from CloudWatch in the isolated test, replayed through the handler logic."""

    def test_fixture_has_no_account_id(self):
        text = (FIXTURES / "continuous-failure-events.json").read_text()
        self.assertEqual(re.findall(r"\b(?!000000000000)\d{12}\b", text), [])

    def test_alarm_then_normalized(self):
        two = Two()
        self.assertEqual(two.run("cf", fixture("initial_ok"))["sent"], [])
        self.assertEqual(two.run("cf", fixture("alarm"))["sent"], ["alarm"])
        self.assertEqual(two.run("cf", fixture("ok_after_alarm"))["sent"], ["normalized"])
        self.assertIn("Alarm: OK -> ALARM at 02:18:30 JST", two.sent[0])
        self.assertTrue(two.sent[1].startswith(
            "HIGH — Availability Continuous Failure: NORMALIZED (alarm back to OK)"))

    def test_probe_stop_during_alarm_is_data_insufficient(self):
        two = Two()
        two.run("cf", fixture("alarm"))
        result = two.run("cf", fixture("probe_stopped"))
        self.assertEqual(result["sent"], ["insufficient"])
        self.assertTrue(two.sent[1].startswith("WARN — Availability Continuous Failure: DATA INSUFFICIENT"))
        self.assertNotIn("NORMALIZED", two.sent[1])

    def test_alarm_across_a_missing_minute_is_accepted(self):
        two = Two()
        self.assertEqual(two.run("cf", fixture("alarm_across_gap"))["sent"], ["alarm"])

    def test_metric_math_event_parses(self):
        change = br.parse(fixture("metric_math_alarm"), alarm_name=CF_NAME, alarm_arn=CF_ARN)
        self.assertEqual((change["alarm_state"], change["previous_state"], change["state_at"]),
                         ("ALARM", "OK", "2026-11-27T17:16:45.057Z"))
        self.assertEqual(change["reason_kind"], "evaluated")

    def test_real_events_are_rejected_for_the_other_alarm(self):
        for name in ("alarm", "metric_math_alarm"):
            self.assertEqual(br.parse(fixture(name), alarm_name=NAME, alarm_arn=ARN),
                             "unmonitored alarm")


def tf(name):
    return (ROOT / name).read_text()


def block(text, kind, name):
    return re.search(rf'resource "{kind}" "{name}" \{{(.*?)\n\}}', text, re.S).group(1)


def value(text, attribute):
    return re.search(rf"^\s*{attribute}\s*=\s*(.+?)\s*$", text, re.M).group(1).strip('"')


class TerraformConsistencyTests(unittest.TestCase):
    def test_alarm_matches_the_tested_configuration(self):
        alarm = block(tf("continuous_failure.tf"), "aws_cloudwatch_metric_alarm", "continuous_failure")
        expected = {"namespace": "GMS/Availability", "metric_name": "HTTPUp", "statistic": "Minimum",
                    "period": "60", "evaluation_periods": str(br.CONTINUOUS_EVALUATION_PERIODS),
                    "datapoints_to_alarm": str(br.CONTINUOUS_DATAPOINTS_TO_ALARM), "threshold": "1",
                    "comparison_operator": "LessThanThreshold", "treat_missing_data": "missing"}
        for attribute, want in expected.items():
            self.assertEqual(value(alarm, attribute), want, attribute)
        self.assertEqual((br.CONTINUOUS_EVALUATION_PERIODS, br.CONTINUOUS_DATAPOINTS_TO_ALARM), (6, 6))
        self.assertIn('dimensions        = { Target = "sunlightjetrans.com" }', alarm)
        self.assertIn(f'continuous_failure_alarm_name = "{CF_NAME}"', tf("continuous_failure.tf"))
        for absent in ("metric_query", "evaluation_window", "unit"):
            self.assertNotRegex(alarm, rf"(?m)^\s*{absent}\b", absent)
        for actions in ("alarm_actions", "ok_actions", "insufficient_data_actions"):
            self.assertEqual(value(alarm, actions), "[]")

    def test_rule_matches_only_this_alarm_and_is_gated(self):
        rule = block(tf("continuous_failure.tf"), "aws_cloudwatch_event_rule", "continuous_failure")
        self.assertIn("resources     = [aws_cloudwatch_metric_alarm.continuous_failure.arn]", rule)
        self.assertIn("alarmName = [aws_cloudwatch_metric_alarm.continuous_failure.alarm_name]", rule)
        self.assertIn('state = var.continuous_failure_alarm_rollout == "off" ? "DISABLED" : "ENABLED"', rule)
        permission = block(tf("continuous_failure.tf"), "aws_lambda_permission", "continuous_failure")
        self.assertIn("source_arn    = aws_cloudwatch_event_rule.continuous_failure.arn", permission)
        main = tf("main.tf")
        self.assertIn('CONTINUOUS_FAILURE_DELIVERY   = var.continuous_failure_alarm_rollout == "live" '
                      '? "enabled" : "disabled"', main)
        self.assertIn("CONTINUOUS_FAILURE_ALARM_ARN  = local.continuous_failure_alarm_arn", main)
        self.assertIn("CONTINUOUS_FAILURE_ALARM_NAME = local.continuous_failure_alarm_name", main)
        cf = tf("continuous_failure.tf")
        self.assertIn('continuous_failure_alarm_arn  = "arn:aws:cloudwatch:${var.aws_region}:'
                      '${data.aws_caller_identity.current.account_id}:alarm:'
                      '${local.continuous_failure_alarm_name}"', cf)
        self.assertIn("alarm_name        = local.continuous_failure_alarm_name",
                      block(cf, "aws_cloudwatch_metric_alarm", "continuous_failure"))
        # The constructed ARN has the same form as the burn rate alarm's real ARN.
        self.assertRegex(ARN, r"^arn:aws:cloudwatch:ap-northeast-1:\d{12}:alarm:" + re.escape(NAME) + "$")
        rollout = re.search(r'variable "continuous_failure_alarm_rollout" \{(.*?)\n\}',
                            tf("variables.tf"), re.S).group(1)
        self.assertIn('default = "off"', rollout)

    def test_no_iam_change_and_burn_rate_resources_untouched(self):
        self.assertNotIn("aws_iam", tf("continuous_failure.tf"))
        self.assertNotIn("continuous", tf("burn_rate.tf"))
        rule = block(tf("burn_rate.tf"), "aws_cloudwatch_event_rule", "burn_rate")
        self.assertNotIn("continuous", rule)


if __name__ == "__main__":
    unittest.main()
