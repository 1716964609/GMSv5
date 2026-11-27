"""Responder context tests (Now line, last 60 minutes). No AWS calls.

CloudWatch is a scripted stub and DynamoDB the in-memory FakeTable. The
production HTTP state row and HTTPUp metric are never read here; that
connection is checked in SHADOW (Availability Runbook §13.5).
"""
import json
import math
import os
import random
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "lambda"))

import burn_rate as br  # noqa: E402
import burn_rate_context as brc  # noqa: E402
from fake_dynamodb import FakeTable  # noqa: E402
from test_burn_rate import ARN, NAME, T1, T2, Harness, alarm_event  # noqa: E402

UTC = timezone.utc
# Window for T1 = 2026-11-24T00:06:40.989Z: [23:06, 00:06) UTC = 08:06-09:06 JST.
START = datetime(2026, 11, 23, 23, 6, tzinfo=UTC)
END = datetime(2026, 11, 24, 0, 6, tzinfo=UTC)
NOW = datetime(2026, 11, 24, 0, 8, 10, tzinfo=UTC)


def minutes(pattern, start=START):
    """'F' failed (0), 'O' ok (1), '.' missing; one character per minute."""
    stamps, values = [], []
    for i, mark in enumerate(pattern):
        if mark != ".":
            stamps.append(start + timedelta(minutes=i))
            values.append(0.0 if mark == "F" else 1.0)
    return stamps, values


def summary_of(pattern, **kw):
    stamps, values = minutes(pattern)
    return brc.summarize(stamps, values, START, END, **kw)


def client_error(code):
    return ClientError({"Error": {"Code": code, "Message": "secret detail " + "x" * 20}},
                       "GetMetricData")


class StubCloudWatch:
    """Returns scripted pages (dicts) or raises scripted exceptions, in order."""

    def __init__(self, *pages):
        self.pages = list(pages)
        self.calls = []

    def get_metric_data(self, **kwargs):
        self.calls.append(kwargs)
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


def page(stamps, values, code="Complete", token=None):
    result = {"Id": "httpup", "Label": "HTTPUp", "Timestamps": stamps, "Values": values,
              "StatusCode": code}
    body = {"MetricDataResults": [result], "Messages": []}
    if token:
        body["NextToken"] = token
    return body


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def http_row(**fields):
    row = {"instance_id": "http#sunlightjetrans.com", "event_time": "~STATE"}
    row.update(fields)
    return row


class WindowTests(unittest.TestCase):
    def test_window_is_sixty_completed_minutes_before_the_state_timestamp(self):
        self.assertEqual(brc.window(br.state_time(T1)), (START, END))
        # Exactly on a minute boundary: that minute is not complete, so it is excluded.
        start, end = brc.window("2026-11-24T00:06:00.000Z")
        self.assertEqual((start, end), (START, END))

    def test_slots_always_add_up_to_sixty(self):
        rng = random.Random(7)
        for _ in range(300):
            stamps, values = [], []
            for _ in range(rng.randint(0, 130)):
                stamps.append(START + timedelta(minutes=rng.randint(-10, 70),
                                                seconds=rng.choice((0, 0, 0, 30))))
                values.append(rng.choice((0.0, 1.0, 0.5, 2.0, math.nan, None, "1", True)))
            s = brc.summarize(stamps, values, START, END)
            self.assertEqual(s["failed"] + s["ok"] + s["missing"], 60)
            self.assertLessEqual(s["longest"], s["failed"])
            self.assertLessEqual(s["episodes"], s["failed"])


class PatternTests(unittest.TestCase):
    def test_nine_consecutive_failures(self):
        s = summary_of("O" * 30 + "F" * 9 + "O" * 21)
        self.assertEqual((s["failed"], s["ok"], s["missing"]), (9, 51, 0))
        self.assertEqual((s["episodes"], s["longest"]), (1, 9))

    def test_three_by_three(self):
        s = summary_of("O" * 10 + "FFFOOOOFFFOOOOFFF" + "O" * 33)
        self.assertEqual((s["failed"], s["episodes"], s["longest"]), (9, 3, 3))

    def test_missing_minutes_split_episodes(self):
        s = summary_of("O" * 20 + "FF.FF" + "O" * 35)
        self.assertEqual((s["failed"], s["missing"], s["episodes"], s["longest"]), (4, 1, 2, 2))
        lines = brc.window_lines(s)
        self.assertIn("missing minutes split episodes", lines[1])

    def test_all_missing(self):
        s = brc.summarize([], [], START, END)
        self.assertEqual((s["failed"], s["ok"], s["missing"], s["episodes"]), (0, 0, 60, 0))
        self.assertEqual(brc.window_lines(s)[1], "Pattern: no failed minutes")

    def test_no_failures(self):
        s = summary_of("O" * 60)
        self.assertEqual((s["failed"], s["ok"], s["missing"]), (0, 60, 0))
        self.assertEqual(brc.window_lines(s), [
            "Last 60 completed min (08:06 JST–09:06 JST): failed 0 / ok 60 / missing 0",
            "Pattern: no failed minutes"])

    def test_episodes_cut_at_both_window_edges(self):
        s = summary_of("FFF" + "O" * 54 + "FFF")
        self.assertEqual((s["episodes"], s["longest"]), (2, 3))
        self.assertTrue(s["cut_at_start"] and s["cut_at_end"])
        self.assertIn("an episode at the window start and end is counted only inside it",
                      brc.window_lines(s)[1])
        whole = summary_of("F" * 60)
        self.assertEqual((whole["episodes"], whole["longest"]), (1, 60))

    def test_interior_episode_is_not_flagged_as_cut(self):
        s = summary_of("O" + "F" * 58 + "O")
        self.assertFalse(s["cut_at_start"] or s["cut_at_end"])
        self.assertNotIn("counted only inside", brc.window_lines(s)[1])

    def test_example_line(self):
        s = summary_of("O" * 5 + "FFFFF" + "O" * 10 + "FF" + "O" * 10 + "FF" + "O" * 26)
        self.assertEqual(brc.window_lines(s), [
            "Last 60 completed min (08:06 JST–09:06 JST): failed 9 / ok 51 / missing 0",
            "Pattern: 3 observed episodes, longest 5 min"])


class DataQualityTests(unittest.TestCase):
    def test_late_data_is_missing_not_ok(self):
        # The last 3 minutes had not arrived when queried.
        s = summary_of("O" * 50 + "FFFFFFF" + "...")
        self.assertEqual((s["failed"], s["ok"], s["missing"]), (7, 50, 3))

    def test_reversed_response_order(self):
        stamps, values = minutes("O" * 20 + "FFF.F" + "O" * 35)
        forward = brc.summarize(stamps, values, START, END)
        backward = brc.summarize(stamps[::-1], values[::-1], START, END)
        self.assertEqual(forward, backward)

    def test_duplicates_keep_the_minimum(self):
        stamps, values = minutes("O" * 60)
        stamps += [START + timedelta(minutes=10), START + timedelta(minutes=10)]
        values += [0.0, 1.0]
        s = brc.summarize(stamps, values, START, END)
        self.assertEqual((s["failed"], s["ok"], s["missing"], s["ignored"]), (1, 59, 0, 0))

    def test_invalid_values_are_ignored_and_reported(self):
        stamps = [START + timedelta(minutes=i) for i in range(6)]
        values = [math.nan, math.inf, None, "0", True, 0.0]
        s = brc.summarize(stamps, values, START, END)
        self.assertEqual((s["failed"], s["ok"], s["missing"], s["ignored"]), (1, 0, 59, 5))
        self.assertIn("5 unusable datapoints ignored", brc.window_lines(s)[0])

    def test_out_of_range_and_unaligned_timestamps_are_ignored(self):
        stamps = [START - timedelta(minutes=1), END, END + timedelta(minutes=3),
                  START + timedelta(minutes=5, seconds=30),
                  (START + timedelta(minutes=7)).replace(tzinfo=None), "2026-11-23T23:10:00Z"]
        s = brc.summarize(stamps, [0.0] * len(stamps), START, END)
        self.assertEqual((s["failed"], s["missing"], s["ignored"]), (0, 60, 6))

    def test_other_timezone_is_normalized(self):
        jst_stamp = (START + timedelta(minutes=3)).astimezone(timezone(timedelta(hours=9)))
        s = brc.summarize([jst_stamp], [0.0], START, END)
        self.assertEqual(s["failed"], 1)

    def test_values_follow_the_alarm_comparison(self):
        # Failed exactly when value < 1, as LessThanThreshold 1.
        s = brc.summarize([START, START + timedelta(minutes=1), START + timedelta(minutes=2)],
                          [0.99, 1.0, 2.0], START, END)
        self.assertEqual((s["failed"], s["ok"]), (1, 2))


class FetchTests(unittest.TestCase):
    def left(self):
        return 30

    def test_fixed_query_and_half_open_window(self):
        stub = StubCloudWatch(page(*minutes("O" * 60)))
        data = brc.fetch_metric(stub, START, END, self.left)
        self.assertTrue(data["complete"])
        call = stub.calls[0]
        self.assertEqual((call["StartTime"], call["EndTime"]), (START, END))
        self.assertEqual(call["ScanBy"], "TimestampAscending")
        (q,) = call["MetricDataQueries"]
        stat = q["MetricStat"]
        self.assertEqual(stat["Metric"], {"Namespace": "GMS/Availability", "MetricName": "HTTPUp",
                                          "Dimensions": [{"Name": "Target",
                                                          "Value": "sunlightjetrans.com"}]})
        self.assertEqual((stat["Period"], stat["Stat"]), (60, "Minimum"))
        self.assertNotIn("Unit", stat)
        self.assertTrue(q["ReturnData"])

    def test_request_and_response_match_the_api_model(self):
        # botocore validates the request against the CloudWatch service model
        # and parses the response as the real client would. No network call.
        import boto3
        from botocore.stub import Stubber
        session = boto3.Session(aws_access_key_id="test", aws_secret_access_key="test",
                                region_name="ap-northeast-1")
        client = session.client("cloudwatch", config=brc.sdk_config())
        stamps, values = minutes("O" * 58 + "FF")
        with Stubber(client) as stub:
            stub.add_response("get_metric_data", page(stamps, values), {
                "MetricDataQueries": brc.query(), "StartTime": START, "EndTime": END,
                "ScanBy": "TimestampAscending"})
            data = brc.fetch_metric(client, START, END, self.left)
            stub.assert_no_pending_responses()
        s = brc.summarize(data["timestamps"], data["values"], START, END, data["complete"])
        self.assertEqual((s["ok"], s["failed"], s["episodes"]), (58, 2, 1))

    def test_query_matches_the_production_alarm(self):
        block = (ROOT / "burn_rate.tf").read_text()
        alarm = re.search(r'resource "aws_cloudwatch_metric_alarm" "burn_rate_fast" \{(.*?)\n\}',
                          block, re.S).group(1)
        metric = brc.query()[0]["MetricStat"]
        self.assertIn(f'namespace         = "{metric["Metric"]["Namespace"]}"', alarm)
        self.assertIn(f'metric_name       = "{metric["Metric"]["MetricName"]}"', alarm)
        self.assertIn(f'statistic         = "{metric["Stat"]}"', alarm)
        self.assertIn(f'period            = {metric["Period"]}', alarm)
        self.assertIn(f'dimensions        = {{ Target = "{brc.TARGET}" }}', alarm)
        self.assertIn(f"threshold           = {int(brc.THRESHOLD)}", alarm)

    def test_partial_data_follows_next_token(self):
        first = minutes("O" * 30)
        second = minutes("F" * 30, start=START + timedelta(minutes=30))
        stub = StubCloudWatch(page(*first, code="PartialData", token="t1"), page(*second))
        data = brc.fetch_metric(stub, START, END, self.left)
        self.assertTrue(data["complete"])
        self.assertEqual(stub.calls[1]["NextToken"], "t1")
        s = brc.summarize(data["timestamps"], data["values"], START, END, data["complete"])
        self.assertEqual((s["ok"], s["failed"]), (30, 30))

    def test_partial_data_without_more_pages_is_flagged(self):
        stub = StubCloudWatch(page(*minutes("O" * 20), code="PartialData"))
        data = brc.fetch_metric(stub, START, END, self.left)
        self.assertFalse(data["complete"])
        s = brc.summarize(data["timestamps"], data["values"], START, END, data["complete"])
        self.assertIn("partial API response", brc.window_lines(s)[0])

    def test_page_limit_is_bounded(self):
        pages = [page(*minutes("O"), code="PartialData", token=f"t{i}") for i in range(5)]
        stub = StubCloudWatch(*pages)
        data = brc.fetch_metric(stub, START, END, self.left)
        self.assertEqual(len(stub.calls), brc.MAX_PAGES)
        self.assertFalse(data["complete"])

    def test_status_and_shape_errors(self):
        for body, name in ((page([], [], code="Forbidden"), "Forbidden"),
                           (page([], [], code="InternalError"), "InternalError"),
                           ({"MetricDataResults": []}, "no result for the query"),
                           (page([START], [], code="Complete"), "malformed result"),
                           ({"MetricDataResults": [{"Id": "other"}]}, "no result for the query")):
            with self.assertRaises(brc.QueryFailed) as caught:
                brc.fetch_metric(StubCloudWatch(body), START, END, self.left)
            self.assertEqual(brc.error_name(caught.exception), name)

    def test_no_call_without_budget(self):
        stub = StubCloudWatch()
        with self.assertRaises(brc.BudgetExhausted):
            brc.fetch_metric(stub, START, END, lambda: brc.CALL_WORST_SECONDS - 1)
        self.assertEqual(stub.calls, [])


class NowTests(unittest.TestCase):
    def line(self, row, now=NOW):
        return brc.now_line(brc.http_now(row, now), now)

    def test_up(self):
        row = http_row(last_slot="2026-11-24T00:08", http_status="up", http_failures=0)
        self.assertEqual(self.line(row), "Now: UP (last check 09:08 JST)")

    def test_down_counts_from_first_failed_check(self):
        row = http_row(last_slot="2026-11-24T00:08", http_status="down", http_failures=5,
                       first_fail_at="2026-11-24T00:04:03Z", down_since="2026-11-24T00:04:03Z")
        self.assertEqual(self.line(row), "Now: DOWN — 4 min (since first failed check 09:04 JST); "
                                         "5 consecutive failed checks")

    def test_one_failure_is_not_down_and_not_up(self):
        row = http_row(last_slot="2026-11-24T00:08", http_status="up", http_failures=1,
                       first_fail_at="2026-11-24T00:08:04Z")
        text = self.line(row)
        self.assertTrue(text.startswith("Now: FAILING — 0 min (since first failed check 09:08 JST)"))
        self.assertIn("DOWN not confirmed (2 needed)", text)
        self.assertNotIn("UP", text)

    def test_stale_state_is_unknown_even_when_last_up(self):
        row = http_row(last_slot="2026-11-24T00:01", http_status="up", http_failures=0)
        self.assertEqual(self.line(row),
                         "Now: UNKNOWN — no HTTP check since 09:01 JST (last result: passed)")
        down = http_row(last_slot="2026-11-24T00:01", http_status="down", http_failures=3)
        self.assertIn("(last result: DOWN)", self.line(down))

    def test_staleness_boundary(self):
        row = http_row(last_slot="2026-11-24T00:06", http_status="up", http_failures=0)
        fresh = datetime(2026, 11, 24, 0, 8, 59, tzinfo=UTC)
        stale = datetime(2026, 11, 24, 0, 9, 0, tzinfo=UTC)
        self.assertTrue(self.line(row, fresh).startswith("Now: UP"))
        self.assertTrue(self.line(row, stale).startswith("Now: UNKNOWN"))

    def test_missing_or_malformed_rows_are_unknown(self):
        for row in ({}, http_row(http_status="up"), http_row(last_slot="garbage", http_status="up"),
                    http_row(last_slot="2026-11-24T00:08", http_status="up", http_failures="x"),
                    http_row(last_slot="2026-11-24T00:08", http_status="up", http_failures=-1),
                    http_row(last_slot="2026-11-24T00:08", http_failures=0),
                    http_row(last_slot="2026-11-24T00:20", http_status="up", http_failures=0)):
            self.assertTrue(self.line(row).startswith("Now: UNKNOWN"), row)

    def test_down_without_first_failure_time(self):
        row = http_row(last_slot="2026-11-24T00:08", http_status="down", http_failures=2)
        self.assertEqual(self.line(row), "Now: DOWN — since: unknown; 2 consecutive failed checks")

    def test_reads_the_existing_row_with_the_existing_key(self):
        table = FakeTable()
        table.items[("http#sunlightjetrans.com", "~STATE")] = http_row(last_slot="x")
        self.assertEqual(brc.fetch_http_row(table)["last_slot"], "x")
        self.assertEqual(table.calls[0][0], "get_item")
        handler_source = (ROOT / "lambda" / "handler.py").read_text()
        self.assertIn('HTTP_STATE_ID = "http#sunlightjetrans.com"', handler_source)
        for field in ("last_slot", "http_failures", "http_status", "first_fail_at", "down_since"):
            self.assertIn(f'"{field}"', handler_source)
        self.assertIn("if failures < 2", handler_source)  # CONFIRM_FAILURES


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.row = http_row(last_slot="2026-11-24T00:08", http_status="up", http_failures=0)

    def context(self, cw, row=None, lambda_remaining=None, row_error=None):
        def read():
            if row_error:
                raise row_error
            return self.row if row is None else row
        return brc.Context(http_row=read,
                           metrics=lambda s, e, left: brc.fetch_metric(cw, s, e, left),
                           now=lambda: NOW, clock=self.clock, lambda_remaining=lambda_remaining)

    def test_lines(self):
        cw = StubCloudWatch(page(*minutes("O" * 51 + "F" * 9)))
        self.assertEqual(self.context(cw).lines(br.state_time(T1)), [
            "Now: UP (last check 09:08 JST)",
            "Last 60 completed min (08:06 JST–09:06 JST): failed 9 / ok 51 / missing 0",
            "Pattern: 1 observed episode, longest 9 min (an episode at the window end is "
            "counted only inside it)"])

    def test_window_uses_the_event_not_the_clock(self):
        cw = StubCloudWatch(page([], []))
        ctx = brc.Context(http_row=lambda: self.row,
                          metrics=lambda s, e, left: brc.fetch_metric(cw, s, e, left),
                          now=lambda: NOW + timedelta(hours=5), clock=self.clock)
        ctx.lines(br.state_time(T1))
        self.assertEqual((cw.calls[0]["StartTime"], cw.calls[0]["EndTime"]), (START, END))

    def test_api_errors_only_drop_that_line(self):
        cases = ((client_error("AccessDenied"), "AccessDenied"),
                 (client_error("ThrottlingException"), "ThrottlingException"),
                 (ReadTimeoutError(endpoint_url="https://monitoring"), "ReadTimeoutError"),
                 (ConnectTimeoutError(endpoint_url="https://monitoring"), "ConnectTimeoutError"))
        for exc, name in cases:
            lines = self.context(StubCloudWatch(exc)).lines(br.state_time(T1))
            self.assertEqual(lines, ["Now: UP (last check 09:08 JST)",
                                     f"Last 60 completed min: unavailable ({name})"])
            self.assertNotIn("secret detail", "\n".join(lines))

    def test_now_failure_only_drops_that_line(self):
        cw = StubCloudWatch(page(*minutes("O" * 60)))
        lines = self.context(cw, row_error=client_error("ProvisionedThroughputExceededException")
                             ).lines(br.state_time(T1))
        self.assertEqual(lines[0], "Now: UNKNOWN — HTTP state unavailable "
                                   "(ProvisionedThroughputExceededException)")
        self.assertTrue(lines[1].startswith("Last 60 completed min (08:06 JST"))

    def test_budget_exhausted_by_a_slow_now_read(self):
        cw = StubCloudWatch()

        def slow():
            self.clock.t += 20
            return self.row
        ctx = brc.Context(http_row=slow, now=lambda: NOW, clock=self.clock,
                          metrics=lambda s, e, left: brc.fetch_metric(cw, s, e, left))
        lines = ctx.lines(br.state_time(T1))
        self.assertEqual(lines[1], "Last 60 completed min: unavailable (time budget)")
        self.assertEqual(cw.calls, [])

    def test_lambda_time_left_limits_lookups(self):
        cw = StubCloudWatch()
        ctx = self.context(cw, lambda_remaining=lambda: brc.NOTIFY_RESERVE_SECONDS + 5)
        lines = ctx.lines(br.state_time(T1))
        self.assertEqual(lines, ["Now: UNKNOWN — HTTP state unavailable (time budget)",
                                 "Last 60 completed min: unavailable (time budget)"])
        self.assertEqual(cw.calls, [])


def live_context(cw=None, row=None):
    clock = Clock()
    cw = cw or StubCloudWatch(*[page(*minutes("O" * 51 + "F" * 9)) for _ in range(5)])
    row = row or http_row(last_slot="2026-11-24T00:08", http_status="down", http_failures=4,
                          first_fail_at="2026-11-24T00:04:03Z", down_since="2026-11-24T00:04:03Z")
    ctx = brc.Context(http_row=lambda: row, now=lambda: NOW, clock=clock,
                      metrics=lambda s, e, left: brc.fetch_metric(cw, s, e, left))
    return ctx, cw


class FlowWithContextTests(unittest.TestCase):
    """The context is read only for messages that are sent (or logged in shadow)."""

    def run_with(self, h, event, ctx):
        return br.handle(event, table=h.table, notify=h.notify, now=lambda: h.clock,
                         alarm_name=NAME, alarm_arn=ARN, delivery=h.delivery, context=ctx)

    def test_alarm_message_with_context(self):
        h = Harness()
        ctx, cw = live_context()
        self.assertEqual(self.run_with(h, alarm_event("ALARM", T1), ctx)["sent"], ["alarm"])
        lines = h.sent[0].split("\n")
        self.assertEqual(lines[:5], [
            "HIGH — Availability Burn Rate: ALARM",
            "Alarm threshold: 9/60 failed datapoints",
            "Now: DOWN — 4 min (since first failed check 09:04 JST); 4 consecutive failed checks",
            "Last 60 completed min (08:06 JST–09:06 JST): failed 9 / ok 51 / missing 0",
            "Pattern: 1 observed episode, longest 9 min (an episode at the window end is "
            "counted only inside it)"])
        self.assertEqual(len(cw.calls), 1)

    def test_duplicates_make_no_extra_calls(self):
        h = Harness()
        ctx, cw = live_context()
        self.run_with(h, alarm_event("ALARM", T1), ctx)
        reads = len([c for c in h.table.calls if c[0] == "get_item"])
        for _ in range(3):
            self.assertEqual(self.run_with(h, alarm_event("ALARM", T1), ctx)["sent"], [])
        self.assertEqual((len(cw.calls), ctx.calls), (1, {"http_row": 1, "metrics": 1}))
        self.assertEqual(len(h.sent), 1)
        self.assertGreater(len([c for c in h.table.calls if c[0] == "get_item"]), reads)

    def test_stale_event_makes_no_extra_calls(self):
        h = Harness()
        ctx, cw = live_context()
        self.run_with(h, alarm_event("ALARM", T1), ctx)
        self.run_with(h, alarm_event("OK", T2, previous="ALARM"), ctx)
        self.assertEqual(len(cw.calls), 2)
        self.run_with(h, alarm_event("ALARM", T1), ctx)  # older than the stored OK
        self.assertEqual(len(cw.calls), 2)

    def test_held_lease_makes_no_calls(self):
        h = Harness()
        ctx, cw = live_context()
        h.run(alarm_event("ALARM", T1))
        h.table.items[("burnrate#" + NAME, "~STATE")].update(
            notified_state="OK", lease_at=br.state_time(T1),
            lease_until=int(h.clock.timestamp()) + 60)
        with self.assertRaises(br.NotificationInFlight):
            self.run_with(h, alarm_event("ALARM", T1), ctx)
        self.assertEqual(cw.calls, [])

    def test_initial_ok_makes_no_calls(self):
        h = Harness()
        ctx, cw = live_context()
        self.run_with(h, alarm_event("OK", T1, previous="INSUFFICIENT_DATA"), ctx)
        self.assertEqual((h.sent, cw.calls), ([], []))

    def test_context_failure_still_notifies(self):
        h = Harness()
        cw = StubCloudWatch(client_error("AccessDenied"))
        ctx = brc.Context(http_row=lambda: (_ for _ in ()).throw(TimeoutError()),
                          metrics=lambda s, e, left: brc.fetch_metric(cw, s, e, left),
                          now=lambda: NOW, clock=Clock())
        self.assertEqual(self.run_with(h, alarm_event("ALARM", T1), ctx)["sent"], ["alarm"])
        self.assertIn("Now: UNKNOWN — HTTP state unavailable (TimeoutError)", h.sent[0])
        self.assertIn("Last 60 completed min: unavailable (AccessDenied)", h.sent[0])
        self.assertEqual(h.row()["notified_state"], "ALARM")

    def test_retry_after_failed_send_reads_context_again(self):
        h = Harness()
        ctx, cw = live_context()
        h.fail = 1
        with self.assertLogs(br.LOG, "ERROR"):
            with self.assertRaises(OSError):
                self.run_with(h, alarm_event("ALARM", T1), ctx)
        self.assertEqual(self.run_with(h, alarm_event("ALARM", T1), ctx)["sent"], ["alarm"])
        self.assertEqual(len(cw.calls), 2)

    def test_three_kinds_differ(self):
        h = Harness()
        ctx, _ = live_context()
        self.run_with(h, alarm_event("ALARM", T1), ctx)
        self.run_with(h, alarm_event("OK", T2, previous="ALARM"), ctx)
        self.run_with(h, alarm_event("INSUFFICIENT_DATA", "2026-11-24T03:00:00.000+0000",
                                     previous="OK"), ctx)
        heads = [text.split("\n")[0] for text in h.sent]
        self.assertEqual(heads, ["HIGH — Availability Burn Rate: ALARM",
                                 "HIGH — Availability Burn Rate: NORMALIZED (alarm back to OK)",
                                 "WARN — Availability Burn Rate: DATA INSUFFICIENT"])
        self.assertIn("NOT when the site recovered", h.sent[1])
        self.assertIn("not proof of downtime or recovery", h.sent[2])
        self.assertNotIn("NORMALIZED", h.sent[2])


class ShadowContextTests(unittest.TestCase):
    def test_shadow_logs_the_full_message_and_sends_nothing(self):
        h = Harness(delivery="disabled")
        ctx, cw = live_context()
        with self.assertLogs(br.LOG, "INFO") as logs:
            result = br.handle(alarm_event("ALARM", T1), table=h.table, notify=h.notify,
                               now=lambda: h.clock, alarm_name=NAME, alarm_arn=ARN,
                               delivery="disabled", context=ctx)
        self.assertEqual((result["sent"], h.sent), (["shadow:alarm"], []))
        records = [json.loads(r.getMessage()) for r in logs.records
                   if r.getMessage().startswith("{") and '"shadow"' in r.getMessage()]
        (record,) = records
        self.assertEqual(record["would_send"], "alarm")
        self.assertTrue(record["message"].startswith("HIGH — Availability Burn Rate: ALARM\n"))
        self.assertIn("Now: DOWN — 4 min", record["message"])
        self.assertIn("failed 9 / ok 51 / missing 0", record["message"])
        self.assertNotIn("notified_state", h.row())

    def test_shadow_duplicate_is_not_logged_or_fetched_again(self):
        h = Harness(delivery="disabled")
        ctx, cw = live_context()
        args = dict(table=h.table, notify=h.notify, now=lambda: h.clock, alarm_name=NAME,
                    alarm_arn=ARN, delivery="disabled", context=ctx)
        br.handle(alarm_event("ALARM", T1), **args)
        self.assertEqual(br.handle(alarm_event("ALARM", T1), **args)["sent"], [])
        self.assertEqual(len(cw.calls), 1)
        self.assertEqual(h.sent, [])


class PreviewTests(unittest.TestCase):
    def test_preview_renders_four_texts_reads_once_and_writes_nothing(self):
        ctx, cw = live_context()
        with self.assertLogs(br.LOG, "INFO"):
            result = br.preview(context=ctx, now=lambda: NOW, alarm_name=NAME)
        texts = result["preview"]
        self.assertEqual(set(texts), {"alarm", "normalized", "insufficient", "data_restored"})
        for text in texts.values():
            self.assertTrue(text.startswith("PREVIEW (not sent)\n"))
            self.assertIn("Now: DOWN — 4 min", text)
        # Window ends at the preview time: [23:08, 00:08) UTC.
        self.assertEqual(cw.calls[0]["EndTime"], datetime(2026, 11, 24, 0, 8, tzinfo=UTC))
        self.assertEqual((len(cw.calls), ctx.calls), (1, {"http_row": 1, "metrics": 1}))


class LambdaWiringTests(unittest.TestCase):
    def setUp(self):
        brc.cloudwatch.cache_clear()
        brc.table.cache_clear()
        self.addCleanup(brc.cloudwatch.cache_clear)
        self.addCleanup(brc.table.cache_clear)

    def test_bounded_clients_in_tokyo(self):
        calls = []
        with patch("boto3.client", side_effect=lambda *a, **k: calls.append((a, k)) or "cw"):
            brc.cloudwatch()
        (args, kwargs), = calls
        self.assertEqual(args, ("cloudwatch",))
        self.assertEqual(kwargs["region_name"], "ap-northeast-1")
        config = kwargs["config"]
        self.assertEqual((config.connect_timeout, config.read_timeout), (2, 3))
        self.assertEqual(config.retries, {"mode": "standard", "total_max_attempts": 2})

        class Resource:
            def Table(self, name):  # noqa: N802 - boto3 API
                return ("table", name)
        resources = []
        with patch.dict(os.environ, {"TABLE_NAME": "t"}), \
                patch("boto3.resource", side_effect=lambda *a, **k: resources.append(k) or Resource()):
            self.assertEqual(brc.table(), ("table", "t"))
        self.assertEqual(resources[0]["config"].retries["total_max_attempts"], 2)

    def test_time_limits_fit_inside_the_lease(self):
        per_call = brc.TOTAL_ATTEMPTS * (brc.CONNECT_TIMEOUT + brc.READ_TIMEOUT)
        self.assertGreater(brc.CALL_WORST_SECONDS, per_call)
        # Context + notify (8 s urlopen) + DynamoDB writes stay well inside the 90 s lease.
        self.assertLess(brc.BUDGET_SECONDS + 8 + 10, br.LEASE_SECONDS)
        self.assertGreaterEqual(brc.NOTIFY_RESERVE_SECONDS, 8 + 10)

    def test_nothing_from_the_event_selects_the_metric(self):
        event = alarm_event("ALARM", T1)
        event["detail"]["configuration"] = {"metrics": [{"metricStat": {"metric": {
            "namespace": "Other", "name": "Secret", "dimensions": {"Target": "x"}}}}]}
        h = Harness()
        ctx, cw = live_context()
        br.handle(event, table=h.table, notify=h.notify, now=lambda: h.clock, alarm_name=NAME,
                  alarm_arn=ARN, delivery="enabled", context=ctx)
        self.assertEqual(cw.calls[0]["MetricDataQueries"], brc.query())

    def test_rejected_events_make_no_calls(self):
        h = Harness()
        ctx, cw = live_context()
        other = alarm_event("ALARM", T1, arn=ARN.replace("burn-rate-fast", "other"))
        result = br.handle(other, table=h.table, notify=h.notify, now=lambda: h.clock,
                           alarm_name=NAME, alarm_arn=ARN, delivery="enabled", context=ctx)
        self.assertEqual(result, {"ignored": "unmonitored alarm ARN"})
        self.assertEqual((cw.calls, ctx.calls), ([], {"http_row": 0, "metrics": 0}))


class IamTests(unittest.TestCase):
    def test_only_get_metric_data_added_and_region_bound(self):
        main = (ROOT / "main.tf").read_text()
        policy = re.search(r'data "aws_iam_policy_document" "lambda_runtime" \{(.*?)\n\}\n',
                           main, re.S).group(1)
        actions = [a for block in re.findall(r"actions\s*=\s*\[(.*?)\]", policy, re.S)
                   for a in re.findall(r'"([^"]+)"', block)]
        self.assertEqual(sorted(set(actions)), sorted({
            "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query",
            "cloudwatch:PutMetricData", "ssm:GetParameter", "ec2:DescribeInstances",
            "ec2:DescribeSpotInstanceRequests", "logs:CreateLogStream", "logs:PutLogEvents",
            "cloudwatch:GetMetricData"}))
        statement = re.search(r'sid\s+= "ReadBurnRateContextMetric"(.*?)\n  \}', policy, re.S).group(1)
        self.assertIn('actions   = ["cloudwatch:GetMetricData"]', statement)
        self.assertIn('variable = "aws:RequestedRegion"', statement)
        self.assertIn("values   = [var.aws_region]", statement)


if __name__ == "__main__":
    unittest.main()
