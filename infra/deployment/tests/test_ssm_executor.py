import json
import subprocess
import sys
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)

import ssm_executor as executor

SHA = "a" * 40
INSTANCE = "i-0123456789abcdef0"
COMMAND = "12345678-1234-1234-1234-123456789012"


class ExecutorTests(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        self.report = Path(temp.name) / "result.json"
        self.calls = []

        self.invocations = [
            self.invocation("Success", 0)
        ]

        patcher = patch(
            "ssm_executor.POLL_INTERVAL", 0
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def invocation(
        self, status, code, marker=True
    ):
        return {
            "CommandId": COMMAND,
            "InstanceId": INSTANCE,
            "Status": status,
            "StatusDetails": status,
            "ResponseCode": code,
            "StandardOutputContent": (
                f"DEPLOYMENT_SUCCEEDED release={SHA}"
                if marker else ""
            ),
        }

    def fake_api(self, operation, *args):
        self.calls.append(operation)

        if operation == "send-command":
            self.assertEqual(
                args[args.index("--document-name") + 1],
                executor.DOCUMENT,
            )

            self.assertEqual(
                args[args.index("--document-version") + 1],
                "1",
            )

            self.assertEqual(
                json.loads(
                    args[args.index("--parameters") + 1]
                ),
                {"ReleaseSha": [SHA]},
            )

            return {
                "Command": {
                    "CommandId": COMMAND
                }
            }

        if operation == "get-command-invocation":
            result = self.invocations.pop(0)

            if isinstance(result, Exception):
                raise result

            return result

        raise AssertionError(operation)

    def run_executor(self):
        with patch(
            "ssm_executor.api",
            side_effect=self.fake_api,
        ):
            return executor.execute(
                INSTANCE, "1", SHA, self.report
            )

    def outcome(self):
        return json.loads(
            self.report.read_text()
        )["outcome"]

    def test_successful_deployment(self):
        self.assertEqual(self.run_executor(), 0)
        self.assertEqual(self.outcome(), "SUCCEEDED")

    def test_pending_and_in_progress(self):
        self.invocations = [
            self.invocation("Pending", -1),
            self.invocation("InProgress", -1),
            self.invocation("Success", 0),
        ]

        self.assertEqual(self.run_executor(), 0)
        self.assertEqual(
            self.calls.count("send-command"), 1
        )

    def test_eventual_consistency(self):
        self.invocations.insert(
            0,
            subprocess.CalledProcessError(
                1, "get-command-invocation"
            ),
        )

        self.assertEqual(self.run_executor(), 0)
        self.assertEqual(
            self.calls.count("send-command"), 1
        )

    def test_failure_before_switch(self):
        self.invocations = [
            self.invocation("Failed", 10)
        ]

        self.assertEqual(self.run_executor(), 10)
        self.assertEqual(
            self.outcome(), "FAILED_BEFORE_SWITCH"
        )

    def test_successful_rollback_fails_workflow(self):
        self.invocations = [
            self.invocation("Failed", 20)
        ]

        self.assertEqual(self.run_executor(), 20)
        self.assertEqual(self.outcome(), "ROLLED_BACK")

    def test_rollback_failure(self):
        self.invocations = [
            self.invocation("Failed", 21)
        ]

        self.assertEqual(self.run_executor(), 21)
        self.assertEqual(
            self.outcome(), "ROLLBACK_FAILED"
        )

    def test_timeout_is_unknown(self):
        self.invocations = [
            self.invocation("TimedOut", -1)
        ]

        self.assertEqual(self.run_executor(), 22)
        self.assertEqual(
            self.outcome(), "OUTCOME_UNKNOWN"
        )

    def test_success_without_marker_is_unknown(self):
        self.invocations = [
            self.invocation("Success", 0, marker=False)
        ]

        self.assertEqual(self.run_executor(), 22)
        self.assertEqual(
            self.outcome(), "OUTCOME_UNKNOWN"
        )

    def test_ambiguous_submission_is_not_retried(self):
        with patch(
            "ssm_executor.api",
            side_effect=subprocess.TimeoutExpired(
                "send-command", 45
            ),
        ) as api:

            result = executor.execute(
                INSTANCE, "1", SHA, self.report
            )

            self.assertEqual(result, 22)
            api.assert_called_once()

        self.assertEqual(
            self.outcome(), "SUBMISSION_UNKNOWN"
        )

    def test_existing_report_blocks_resubmission(self):
        self.run_executor()

        with patch("ssm_executor.api") as api:
            with self.assertRaises(ValueError):
                executor.execute(
                    INSTANCE, "1", SHA, self.report
                )

            api.assert_not_called()

    def test_resume_only_polls_existing_command(self):
        self.run_executor()
        self.calls.clear()

        self.invocations = [
            self.invocation("Success", 0)
        ]

        with patch(
            "ssm_executor.api",
            side_effect=self.fake_api,
        ):
            self.assertEqual(
                executor.resume(self.report), 0
            )

        self.assertEqual(
            self.calls, ["get-command-invocation"]
        )

    def test_invalid_sha_never_sends_command(self):
        with patch("ssm_executor.api") as api:
            with self.assertRaises(ValueError):
                executor.execute(
                    INSTANCE, "1", "../bad", self.report
                )

            api.assert_not_called()

    def test_poll_deadline(self):
        report = {
            "command_id": COMMAND,
            "instance_id": INSTANCE,
            "release_sha": SHA,
        }

        with patch("ssm_executor.api") as api:
            self.assertEqual(
                executor.poll(
                    report, self.report, timeout=0
                ),
                22,
            )

            api.assert_not_called()

        self.assertEqual(
            self.outcome(), "OUTCOME_UNKNOWN"
        )


if __name__ == "__main__":
    unittest.main()
