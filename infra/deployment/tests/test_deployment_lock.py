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

import deployment_lock as lock

SHA = "a" * 40
INSTANCE = "i-0123456789abcdef0"
BUCKET = "gmsv5-releases-123456789012-apne1"
ETAG = '"' + "b" * 32 + '"'


class LockTests(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        self.report = Path(temp.name) / "result.json"
        self.locked = False
        self.operations = []
        self.code = 0
        self.outcome = "SUCCEEDED"

    def fake_aws(self, operation, *args):
        self.operations.append(operation)

        if operation == "put-object":
            self.assertEqual(
                args[args.index("--if-none-match") + 1],
                "*",
            )

            if self.locked:
                raise subprocess.CalledProcessError(
                    1, "lock-conflict"
                )

            self.locked = True
            return json.dumps({"ETag": ETAG})

        if operation == "delete-object":
            self.assertEqual(
                args[args.index("--if-match") + 1],
                ETAG,
            )

            self.locked = False
            return ""

        raise AssertionError(operation)

    def fake_execute(
        self, instance, version, sha, report,
        request_id=None,
    ):
        self.assertTrue(self.locked)

        Path(report).write_text(json.dumps({
            "release_sha": sha,
            "instance_id": instance,
            "request_id": request_id,
            "outcome": self.outcome,
        }))

        return self.code

    def run_deployment(self):
        with patch(
            "deployment_lock.aws",
            side_effect=self.fake_aws,
        ), patch(
            "deployment_lock.execute",
            side_effect=self.fake_execute,
        ):
            return lock.deploy(
                BUCKET, INSTANCE, "1", SHA, self.report
            )

    def test_success_releases_lock(self):
        self.assertEqual(self.run_deployment(), 0)
        self.assertFalse(self.locked)

    def test_rollback_releases_lock(self):
        self.code = 20
        self.outcome = "ROLLED_BACK"
        self.assertEqual(self.run_deployment(), 20)
        self.assertFalse(self.locked)

    def test_before_switch_failure_releases_lock(self):
        self.code = 10
        self.outcome = "FAILED_BEFORE_SWITCH"
        self.assertEqual(self.run_deployment(), 10)
        self.assertFalse(self.locked)

    def test_rollback_failure_retains_lock(self):
        self.code = 21
        self.outcome = "ROLLBACK_FAILED"
        self.assertEqual(self.run_deployment(), 21)
        self.assertTrue(self.locked)

    def test_unknown_result_retains_lock(self):
        self.code = 22
        self.outcome = "OUTCOME_UNKNOWN"
        self.assertEqual(self.run_deployment(), 22)
        self.assertTrue(self.locked)

    def test_existing_lock_blocks_ssm(self):
        self.locked = True

        with patch(
            "deployment_lock.aws",
            side_effect=self.fake_aws,
        ), patch("deployment_lock.execute") as execute:

            with self.assertRaises(
                subprocess.CalledProcessError
            ):
                lock.deploy(
                    BUCKET, INSTANCE, "1", SHA, self.report
                )

            execute.assert_not_called()

    def test_runner_interruption_retains_lock(self):
        with patch(
            "deployment_lock.aws",
            side_effect=self.fake_aws,
        ), patch(
            "deployment_lock.execute",
            side_effect=KeyboardInterrupt,
        ):
            with self.assertRaises(KeyboardInterrupt):
                lock.deploy(
                    BUCKET, INSTANCE, "1", SHA, self.report
                )

        self.assertTrue(self.locked)
        self.assertNotIn("delete-object", self.operations)


if __name__ == "__main__":
    unittest.main()
