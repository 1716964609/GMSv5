import os as _os, sys as _sys  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import no_network  # noqa: E402,F401  Blocks real network access (no AWS calls).
import sys
import unittest

from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "lambda")
)

import deployment

SHA = "a" * 40
COMMAND = "12345678-1234-1234-1234-123456789012"


class DeploymentTests(unittest.TestCase):

    def setUp(self):
        self.ssm = Mock()
        self.save = Mock(return_value=True)
        self.notify = Mock()
        self.metric = Mock()

        self.event = {
            "source": "aws.ssm",
            "detail-type": deployment.EVENT_TYPE,
            "id": "test-event",
            "time": "2026-11-18T15:41:00Z",
            "detail": {
                "document-name": deployment.DOCUMENT,
                "instance-id": "i-app",
                "command-id": COMMAND,
                "status": "Success",
            },
        }

        self.ssm.get_command_invocation.return_value = {
            "CommandId": COMMAND,
            "InstanceId": "i-app",
            "Status": "Success",
            "ResponseCode": 0,
            "StandardOutputContent": (
                f"DEPLOYMENT_REQUESTED sha={SHA}\n"
                "2026-11-18T15:41:00Z "
                f"DEPLOYMENT_SUCCEEDED release={SHA}\n"
            ),
        }

    def handle(self):
        return deployment.handle(
            self.event,
            app="i-app",
            ssm=self.ssm,
            save=self.save,
            notify=self.notify,
            metric=self.metric,
        )

    def test_success(self):
        result = self.handle()
        self.assertEqual(result["outcome"], "SUCCEEDED")
        self.assertEqual(result["release"], SHA)
        self.notify.assert_called_once()
        self.metric.assert_called_once()

    def test_start(self):
        self.event["detail"]["status"] = "InProgress"

        self.assertEqual(
            self.handle()["outcome"], "IN_PROGRESS"
        )

        self.ssm.get_command_invocation.assert_not_called()

    def test_duplicate(self):
        self.save.return_value = False

        self.assertEqual(
            self.handle(), {"duplicate": True}
        )

        self.notify.assert_not_called()
        self.metric.assert_not_called()

    def test_separate_partition(self):
        self.handle()

        self.assertEqual(
            self.save.call_args.args[0],
            "deployment#i-app",
        )

        self.assertEqual(
            self.save.call_args.kwargs["stable_key"],
            f"command#{COMMAND}#finished",
        )

    def test_foreign_document(self):
        self.event["detail"]["document-name"] = (
            "AWS-RunShellScript"
        )

        self.assertIn("ignored", self.handle())
        self.save.assert_not_called()

    def test_foreign_instance(self):
        self.event["detail"]["instance-id"] = "i-other"

        self.assertIn("ignored", self.handle())
        self.save.assert_not_called()

    def test_rollback_success(self):
        self.event["detail"]["status"] = "Failed"

        result = self.ssm.get_command_invocation.return_value
        result.update(Status="Failed", ResponseCode=20)

        self.assertEqual(
            self.handle()["outcome"], "ROLLED_BACK"
        )

    def test_rollback_failure(self):
        self.event["detail"]["status"] = "Failed"

        result = self.ssm.get_command_invocation.return_value
        result.update(Status="Failed", ResponseCode=21)

        self.assertEqual(
            self.handle()["outcome"], "ROLLBACK_FAILED"
        )

    def test_failure_before_switch(self):
        self.event["detail"]["status"] = "Failed"

        result = self.ssm.get_command_invocation.return_value
        result.update(Status="Failed", ResponseCode=10)

        self.assertEqual(
            self.handle()["outcome"],
            "FAILED_BEFORE_SWITCH",
        )

    def test_timeout(self):
        self.event["detail"]["status"] = "TimedOut"

        result = self.ssm.get_command_invocation.return_value
        result.update(Status="TimedOut", ResponseCode=-1)

        self.assertEqual(
            self.handle()["outcome"], "OUTCOME_UNKNOWN"
        )

    def test_cancelled(self):
        self.event["detail"]["status"] = "Canceled"

        result = self.ssm.get_command_invocation.return_value
        result.update(Status="Cancelled", ResponseCode=-1)

        self.assertEqual(
            self.handle()["outcome"], "OUTCOME_UNKNOWN"
        )

    def test_success_without_marker(self):
        result = self.ssm.get_command_invocation.return_value
        result["StandardOutputContent"] = ""

        self.assertEqual(
            self.handle()["outcome"], "OUTCOME_UNKNOWN"
        )

    @patch("deployment.time.sleep")
    def test_terminal_result_delayed(self, sleep):
        result = self.ssm.get_command_invocation.return_value

        self.ssm.get_command_invocation.side_effect = [
            {**result, "Status": "InProgress"},
            result,
        ]

        self.assertEqual(
            self.handle()["outcome"], "SUCCEEDED"
        )

        sleep.assert_called_once()

    def test_mismatched_command_identity(self):
        result = self.ssm.get_command_invocation.return_value
        result["CommandId"] = "unexpected"

        with self.assertRaises(ValueError):
            self.handle()

        self.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
