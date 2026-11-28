"""Discord notification priority (HIGH mentions one user, LOW mentions nobody). No AWS calls.

HIGH: ALARM and DATA INSUFFICIENT of both alarms, and the HIGH synthetic test.
LOW: everything else (NORMALIZED, DATA RESTORED, HTTP, EC2, Spot, deployment,
the default synthetic test). The real user ID never appears in this
repository: the test ID below is built at run time and is not a real user.
"""
import os as _os, sys as _sys  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import no_network  # noqa: E402,F401  Blocks real network access (no AWS calls).
import ast  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[2]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "lambda"))

import burn_rate as br  # noqa: E402
from test_burn_rate import ARN, NAME, T1, T2, T3, alarm_event  # noqa: E402
from test_continuous_failure import CF_ARN, CF_NAME, Two, cf_event  # noqa: E402

TEST_ID = "9" * 18  # not a real Discord user; never a literal in the repository
ENV = {"TABLE_NAME": "test-only", "APP_INSTANCE_ID": "i-app", "OBS_INSTANCE_ID": "i-obs",
       "DISCORD_CLOUDFRONT_URL": "https://example.cloudfront.net/notify",
       "DISCORD_TOKEN_PARAMETER": "/test/relay-token"}


def load_handler():
    spec = importlib.util.spec_from_file_location("priority_handler", ROOT / "lambda" / "handler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Captured:
    """Stands in for urlopen: records each request body, answers 204."""

    def __init__(self):
        self.bodies = []

    def __call__(self, request, timeout=None):
        self.bodies.append(json.loads(request.data.decode()))
        response = MagicMock()
        response.status = 204
        response.__enter__ = lambda s: s
        response.__exit__ = lambda s, *a: False
        return response


class HandlerCase(unittest.TestCase):
    def setUp(self):
        self.h = load_handler()
        self.sent = Captured()
        for p in (patch.dict(os.environ, dict(ENV, DISCORD_ALERT_USER_ID=TEST_ID)),
                  patch("urllib.request.urlopen", side_effect=self.sent),
                  patch.object(self.h, "relay_token", return_value="test-token"),
                  patch("boto3.client", side_effect=AssertionError("real AWS client")),
                  patch("boto3.resource", side_effect=AssertionError("real AWS resource"))):
            p.start()
            self.addCleanup(p.stop)

    def assert_low(self, body):
        self.assertEqual(body["allowed_mentions"], {"parse": []})
        self.assertNotIn(TEST_ID, json.dumps(body))

    def assert_high(self, body):
        self.assertEqual(body["allowed_mentions"], {"users": [TEST_ID]})
        self.assertTrue(body["content"].startswith(f"<@{TEST_ID}> "))


class PayloadTests(HandlerCase):
    def test_low_payload_is_unchanged(self):
        self.assertEqual(self.h.discord_payload("hello"),
                         {"content": "hello", "allowed_mentions": {"parse": []}})

    def test_high_payload_mentions_only_that_user_without_parse(self):
        payload = self.h.discord_payload("ALARM", user=TEST_ID)
        self.assertEqual(payload, {"content": f"<@{TEST_ID}> ALARM",
                                   "allowed_mentions": {"users": [TEST_ID]}})
        self.assertNotIn("parse", payload["allowed_mentions"])
        self.assertNotIn("roles", payload["allowed_mentions"])

    def test_mention_survives_truncation(self):
        payload = self.h.discord_payload("x" * 5000, user=TEST_ID)
        self.assertEqual(len(payload["content"]), self.h.DISCORD_CONTENT_LIMIT)
        self.assertTrue(payload["content"].startswith(f"<@{TEST_ID}> x"))
        self.assertEqual(len(self.h.discord_payload("x" * 5000)["content"]), 1800)

    def test_no_everyone_here_role_or_other_user_can_ping(self):
        hostile = f"@everyone @here <@&{'8' * 18}> <@{'7' * 18}> <@!{'7' * 18}>"
        low = self.h.discord_payload(hostile)
        high = self.h.discord_payload(hostile, user=TEST_ID)
        self.assertEqual(low["allowed_mentions"], {"parse": []})  # nothing parsed
        # Only TEST_ID is listed; without parse, roles, everyone and here are not parsed.
        self.assertEqual(high["allowed_mentions"], {"users": [TEST_ID]})

    def test_invalid_or_missing_ids_send_without_mention_and_warn(self):
        for value in ("", "abc", "1234", "1" * 21, " " + TEST_ID, f"<@{TEST_ID}>", TEST_ID + "\n"):
            with self.subTest(value=value), patch.dict(os.environ, {"DISCORD_ALERT_USER_ID": value}):
                with self.assertLogs(self.h.LOG, "WARNING") as logs:
                    self.h.notify_high("ALARM text")
                body = self.sent.bodies[-1]
                self.assertEqual(body, {"content": "ALARM text", "allowed_mentions": {"parse": []}})
                self.assertIn("Discord mention disabled", logs.output[0])
                if value.strip():
                    self.assertNotIn(value.strip(), "\n".join(logs.output))
        with patch.dict(os.environ, {}, clear=False):
            del os.environ["DISCORD_ALERT_USER_ID"]
            with self.assertLogs(self.h.LOG, "WARNING"):
                self.h.notify_high("ALARM text")
        self.assert_low(self.sent.bodies[-1])

    def test_masked_payload_hides_the_id(self):
        high = self.h.masked_payload("ALARM", high=True)
        self.assertEqual(high, {"priority": "high", "mention": True, "payload": {
            "content": "<@USER_ID_MASKED> ALARM", "allowed_mentions": {"users": ["USER_ID_MASKED"]}}})
        self.assertNotIn(TEST_ID, json.dumps(high))
        self.assertEqual(self.h.masked_payload("OK", high=False), {
            "priority": "low", "mention": False,
            "payload": {"content": "OK", "allowed_mentions": {"parse": []}}})
        with patch.dict(os.environ, {"DISCORD_ALERT_USER_ID": ""}), self.assertLogs(self.h.LOG, "WARNING"):
            self.assertEqual(self.h.masked_payload("ALARM", high=True)["mention"], False)


class SendPathTests(HandlerCase):
    def test_notify_is_low_even_with_an_id_configured(self):
        self.h.notify("HTTP DOWN")
        self.assert_low(self.sent.bodies[-1])

    def test_notify_high_sends_the_mention(self):
        self.h.notify_high("ALARM")
        self.assert_high(self.sent.bodies[-1])

    def test_http_down_and_recovered_stay_low(self):
        db = MagicMock()
        db.update_item.side_effect = [
            {"Attributes": {"http_failures": 2, "first_fail_at": "2026-11-16T19:09:00Z",
                            "http_status": "up"}}, {},
            {"Attributes": {"http_status": "down", "down_since": "2026-11-16T19:09:00Z"}}]
        with patch.object(self.h, "table", return_value=db):
            self.h.track_http_status({"time": "2026-11-16T19:10:00Z"}, False)
            self.h.track_http_status({"time": "2026-11-16T19:11:00Z"}, True)
        self.assertEqual(len(self.sent.bodies), 2)
        for body in self.sent.bodies:
            self.assert_low(body)

    def test_spot_warning_stays_low(self):
        event = {"source": "aws.ec2", "detail-type": "EC2 Spot Instance Interruption Warning",
                 "time": "2026-11-16T19:10:00Z", "id": "evt-1",
                 "detail": {"instance-id": "i-app", "instance-action": "stop"}}
        with patch.object(self.h, "save_event", return_value=True), \
                patch.object(self.h, "save_state"), \
                patch.object(self.h, "spot_evidence", return_value=None):
            self.h.handle_ec2(event)
        (body,) = self.sent.bodies
        self.assert_low(body)

    def test_deployment_notifications_use_the_low_notify(self):
        source = (ROOT / "lambda" / "deployment.py").read_text()
        self.assertNotIn("notify_high", source)
        self.assertNotIn("mention", source)

    def test_only_the_intended_calls_can_mention(self):
        calls = {}
        for name in ("handler.py", "burn_rate.py", "deployment.py", "spot_status.py",
                     "burn_rate_context.py"):
            tree = ast.parse((ROOT / "lambda" / name).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id in ("notify", "notify_high", "send"):
                        kw = sorted(k.arg for k in node.keywords)
                        calls.setdefault((name, node.func.id, tuple(kw)), 0)
                        calls[(name, node.func.id, tuple(kw))] += 1
        mention_calls = {k: v for k, v in calls.items() if "mention" in k[2]}
        self.assertEqual(mention_calls, {("handler.py", "notify", ("mention",)): 1})  # in notify_high
        self.assertEqual(calls.get(("handler.py", "notify_high", ())), 1)  # the HIGH synthetic test


class SyntheticTests(HandlerCase):
    def setUp(self):
        super().setUp()
        for p in (patch.object(self.h, "save_event", return_value=True),
                  patch.object(self.h, "metric")):
            p.start()
            self.addCleanup(p.stop)

    def run_test(self, **extra):
        return self.h.handler(dict({"source": "gmsv5.manual-test", "test_mode": True, "id": "t1"},
                                   **extra))

    def test_default_is_low_and_unchanged(self):
        self.assertEqual(self.run_test(), {"synthetic": True, "new": True})
        (body,) = self.sent.bodies
        self.assert_low(body)
        self.assertTrue(body["content"].startswith("[GMS v5 AVAILABILITY TEST] synthetic path OK"))
        self.assertEqual(self.run_test(priority="low"), {"synthetic": True, "new": True})
        self.assert_low(self.sent.bodies[-1])

    def test_high_is_marked_test_and_mentions(self):
        self.assertEqual(self.run_test(priority="high"),
                         {"synthetic": True, "new": True, "priority": "high"})
        (body,) = self.sent.bodies
        self.assert_high(body)
        self.assertIn("[GMS v5 AVAILABILITY TEST] HIGH priority TEST", body["content"])
        self.assertIn("no incident", body["content"])

    def test_invalid_priority_is_rejected_without_side_effects(self):
        for value in ("HIGH", "urgent", "", 1, True, None, ["high"]):
            with self.subTest(value=value):
                self.assertEqual(self.run_test(priority=value),
                                 {"synthetic": True, "rejected": "invalid priority"})
        self.assertEqual(self.sent.bodies, [])
        self.h.save_event.assert_not_called()


class ShadowStageEndToEndTests(HandlerCase):
    """Rule ENABLED + DELIVERY disabled: the production shadow stage, through handler.handler."""

    def setUp(self):
        super().setUp()
        from fake_dynamodb import FakeTable
        self.table = FakeTable()
        env = {"BURN_RATE_ALARM_NAME": NAME, "BURN_RATE_ALARM_ARN": ARN,
               "BURN_RATE_DELIVERY": "disabled", "CONTINUOUS_FAILURE_ALARM_NAME": CF_NAME,
               "CONTINUOUS_FAILURE_ALARM_ARN": CF_ARN, "CONTINUOUS_FAILURE_DELIVERY": "disabled"}

        class Context:
            def lines(self, state_at):
                return ["Now: DOWN — 7 min (since first failed check 08:49 JST); 7 consecutive failed checks",
                        "Last 60 completed min (…): failed 7 / ok 53 / missing 0",
                        "Pattern: 1 observed episode, longest 7 min"]
        for p in (patch.dict(os.environ, env),
                  patch.object(self.h, "table", return_value=self.table),
                  patch.object(self.h, "burn_rate_context", return_value=Context())):
            p.start()
            self.addCleanup(p.stop)

    def run_events(self, events):
        with self.assertLogs(br.LOG, "INFO") as logs:
            results = [self.h.handler(e) for e in events]
        records = [json.loads(r.getMessage()) for r in logs.records
                   if r.getMessage().startswith('{"burn_rate": "shadow"')]
        return results, records, "\n".join(r.getMessage() for r in logs.records)

    def test_both_alarms_alarm_and_normalize_in_shadow(self):
        events = [cf_event("ALARM", T1), alarm_event("ALARM", T1),
                  cf_event("ALARM", T1), alarm_event("ALARM", T1),          # duplicates
                  cf_event("OK", T2, previous="ALARM"), alarm_event("OK", T3, previous="ALARM")]
        results, records, text = self.run_events(events)
        self.assertEqual([r["sent"] for r in results], [
            ["shadow:alarm"], ["shadow:alarm"], [], [], ["shadow:normalized"], ["shadow:normalized"]])
        self.assertEqual(self.sent.bodies, [])                 # nothing reached urlopen
        by = {(r["alarm"], r["would_send"]): r for r in records}
        self.assertEqual(len(records), 4)
        for alarm in ("continuous_failure", "burn_rate"):
            high, low = by[(alarm, "alarm")], by[(alarm, "normalized")]
            self.assertEqual((high["delivered"], high["priority"], high["mention"]), (False, "high", True))
            self.assertEqual(high["payload"]["allowed_mentions"], {"users": ["USER_ID_MASKED"]})
            self.assertTrue(high["payload"]["content"].startswith("<@USER_ID_MASKED> HIGH — "))
            self.assertEqual(high["payload"]["content"], "<@USER_ID_MASKED> " + high["message"])
            self.assertIn("Now: DOWN — 7 min", high["message"])
            self.assertEqual((low["priority"], low["mention"]), ("low", False))
            self.assertEqual(low["payload"]["allowed_mentions"], {"parse": []})
        self.assertNotIn(TEST_ID, text)
        for key in (("burnrate#" + NAME, "~STATE"), ("contfail#" + CF_NAME, "~STATE")):
            row = self.table.row(*key)
            self.assertEqual(row["shadow_notified_state"], "OK")
            self.assertFalse([k for k in ("notified_state", "notified_at", "lease_at", "lease_until")
                              if k in row], row)


class AlarmPriorityTests(unittest.TestCase):
    """ALARM and DATA INSUFFICIENT of both alarms are HIGH; NORMALIZED and DATA RESTORED are LOW."""

    def run_both(self, delivery="enabled"):
        two = Two(br_delivery=delivery, cf_delivery=delivery)
        high, low = [], []
        sequences = {"br": [alarm_event("ALARM", T1), alarm_event("OK", T2, previous="ALARM"),
                            alarm_event("INSUFFICIENT_DATA", T3),
                            alarm_event("OK", "2026-11-24T08:00:00.000+0000",
                                        previous="INSUFFICIENT_DATA")],
                     "cf": [cf_event("ALARM", T1), cf_event("OK", T2, previous="ALARM"),
                            cf_event("INSUFFICIENT_DATA", T3),
                            cf_event("OK", "2026-11-24T08:00:00.000+0000",
                                     previous="INSUFFICIENT_DATA")]}
        kinds = []
        for which, events in sequences.items():
            name, arn, profile = ((NAME, ARN, br.BURN_RATE) if which == "br"
                                  else (CF_NAME, CF_ARN, br.CONTINUOUS_FAILURE))
            for event in events:
                kinds += br.handle(event, table=two.table, notify=low.append, now=lambda: two.clock,
                                   alarm_name=name, alarm_arn=arn, delivery=delivery,
                                   profile=profile, notify_high=high.append)["sent"]
        return kinds, high, low

    def test_kinds_route_to_high_or_low(self):
        kinds, high, low = self.run_both()
        self.assertEqual(kinds, ["alarm", "normalized", "insufficient", "data_restored"] * 2)
        heads = lambda texts: [t.split("\n")[0] for t in texts]  # noqa: E731
        self.assertEqual(heads(high), [
            "HIGH — Availability Burn Rate: ALARM", "WARN — Availability Burn Rate: DATA INSUFFICIENT",
            "HIGH — Availability Continuous Failure: ALARM",
            "WARN — Availability Continuous Failure: DATA INSUFFICIENT"])
        self.assertEqual(heads(low), [
            "HIGH — Availability Burn Rate: NORMALIZED (alarm back to OK)",
            "INFO — Availability Burn Rate: DATA RESTORED",
            "HIGH — Availability Continuous Failure: NORMALIZED (alarm back to OK)",
            "INFO — Availability Continuous Failure: DATA RESTORED"])
        self.assertEqual(br.HIGH_KINDS, frozenset({"alarm", "insufficient"}))

    def test_without_notify_high_everything_uses_notify(self):
        two, sent = Two(), []
        br.handle(alarm_event("ALARM", T1), table=two.table, notify=sent.append,
                  now=lambda: two.clock, alarm_name=NAME, alarm_arn=ARN, delivery="enabled")
        self.assertEqual(len(sent), 1)

    def test_shadow_log_shows_priority_mention_and_masked_payload(self):
        h = load_handler()
        two = Two(br_delivery="disabled")
        with patch.dict(os.environ, {"DISCORD_ALERT_USER_ID": TEST_ID}), \
                self.assertLogs(br.LOG, "INFO") as logs:
            for event in (alarm_event("ALARM", T1), alarm_event("OK", T2, previous="ALARM")):
                br.handle(event, table=two.table, notify=two.notify, now=lambda: two.clock,
                          alarm_name=NAME, alarm_arn=ARN, delivery="disabled",
                          shadow_payload=h.masked_payload, notify_high=h.notify_high)
        records = [json.loads(r.getMessage()) for r in logs.records
                   if r.getMessage().startswith('{"burn_rate": "shadow"')]
        alarm, normalized = records
        self.assertEqual((alarm["priority"], alarm["mention"]), ("high", True))
        self.assertEqual(alarm["payload"]["allowed_mentions"], {"users": ["USER_ID_MASKED"]})
        self.assertEqual(alarm["payload"]["content"], "<@USER_ID_MASKED> " + alarm["message"])
        self.assertEqual((normalized["priority"], normalized["mention"]), ("low", False))
        self.assertEqual(normalized["payload"]["allowed_mentions"], {"parse": []})
        self.assertNotIn(TEST_ID, "\n".join(r.getMessage() for r in logs.records))
        self.assertEqual(two.sent, [])

    def test_shadow_payload_failure_does_not_stop_the_log(self):
        two = Two(br_delivery="disabled")

        def broken(text, high):
            raise RuntimeError("x")
        result = br.handle(alarm_event("ALARM", T1), table=two.table, notify=two.notify,
                           now=lambda: two.clock, alarm_name=NAME, alarm_arn=ARN,
                           delivery="disabled", shadow_payload=broken)
        self.assertEqual(result["sent"], ["shadow:alarm"])

    def test_handler_wires_notify_high_and_the_masked_payload(self):
        source = (ROOT / "lambda" / "handler.py").read_text()
        self.assertIn("notify_high=notify_high,", source)
        self.assertIn("shadow_payload=masked_payload,", source)


def tf(name):
    return (ROOT / name).read_text()


class TerraformTests(unittest.TestCase):
    def test_variable_is_sensitive_empty_by_default_and_validated(self):
        block = re.search(r'variable "discord_alert_user_id" \{(.*?)\n\}', tf("variables.tf"), re.S).group(1)
        self.assertIn("sensitive = true", block)
        self.assertIn('default   = ""', block)
        self.assertIn('condition     = can(regex("^([0-9]{17,20})?$", var.discord_alert_user_id))', block)
        self.assertIn("DISCORD_ALERT_USER_ID = var.discord_alert_user_id", tf("main.tf"))

    def test_live_requires_an_id(self):
        main = tf("main.tf")
        self.assertIn('condition = var.discord_alert_user_id != "" || (\n'
                      '        var.burn_rate_alarm_rollout != "live" && '
                      'var.continuous_failure_alarm_rollout != "live"\n      )', main)

    def test_tfvars_is_not_in_git_and_no_real_id_is_committed(self):
        listing = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True)
        if listing.returncode != 0 or not (ROOT / ".gitignore").exists():
            self.skipTest("not in a git checkout (e.g. a mutation-test copy)")
        self.assertIn("*.tfvars", (ROOT / ".gitignore").read_text())
        tracked = listing.stdout.decode().split("\0")
        self.assertNotIn("infra/terraform/availability/terraform.tfvars", tracked)
        # No 17-20 digit number (a Discord snowflake) in the module or its docs.
        for rel in tracked:
            if not rel or not (rel.startswith("infra/terraform/availability/")
                               or rel.startswith("docs/infrastructure/")):
                continue
            path = REPO / rel
            if path.suffix not in (".py", ".tf", ".md", ".json", ".example", ".txt"):
                continue
            text = path.read_text(errors="ignore")
            self.assertEqual(re.findall(r"(?<![0-9])[0-9]{17,20}(?![0-9])", text), [], rel)


if __name__ == "__main__":
    unittest.main()
