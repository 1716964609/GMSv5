import hashlib
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

import artifact
import publish

SHA = "a" * 40
BUCKET = "gmsv5-releases-123456789012-apne1"


class PublishTests(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        self.directory = Path(temp.name)
        self.objects = {}
        self.uploads = []

        data = b"test-release"
        digest = hashlib.sha256(data).hexdigest()

        (self.directory / artifact.ARTIFACT).write_bytes(data)

        (self.directory / artifact.CHECKSUM).write_text(
            f"{digest}  {artifact.ARTIFACT}\n"
        )

        self.manifest = {
            "manifest_version": 1,
            "commit_sha": SHA,
            "artifact": artifact.ARTIFACT,
            "artifact_sha256": digest,
            "architecture": "arm64",
            "os": "linux",
            "node_version": "v26.9.0",
            "database_schema_fingerprint": "c" * 64,
            "workflow_run_id": "12345",
            "workflow_run_attempt": "1",
        }

        (self.directory / artifact.MANIFEST).write_text(
            json.dumps(self.manifest)
        )

    def key(self, filename):
        return f"releases/{SHA}/{filename}"

    def fake_aws(self, *args):
        operation = args[0]
        key = args[args.index("--key") + 1]

        if operation == "put-object":
            self.uploads.append(key)

            self.assertIn("--if-none-match", args)

            self.assertEqual(
                args[args.index("--if-none-match") + 1],
                "*",
            )

            if key in self.objects:
                raise subprocess.CalledProcessError(
                    1, "conditional-put"
                )

            source = Path(
                args[args.index("--body") + 1]
            )

            self.objects[key] = source.read_bytes()

            return "{}"

        if operation == "head-object":
            return json.dumps({
                "ContentLength": len(self.objects[key]),
                "ETag": '"test-etag"',
            })

        if operation == "get-object":
            self.assertIn("--if-match", args)

            Path(args[-1]).write_bytes(
                self.objects[key]
            )

            return "{}"

        raise AssertionError(operation)

    def run_publish(self):
        with patch(
            "publish.aws",
            side_effect=self.fake_aws,
        ):
            return publish.publish_release(
                BUCKET,
                self.directory,
                SHA,
            )

    def test_successful_publication(self):
        result = self.run_publish()

        self.assertEqual(result, self.manifest)
        self.assertEqual(len(self.objects), 3)

    def test_manifest_is_published_last(self):
        self.run_publish()

        self.assertEqual(
            self.uploads,
            [
                self.key(artifact.ARTIFACT),
                self.key(artifact.CHECKSUM),
                self.key(artifact.MANIFEST),
            ],
        )

    def test_identical_artifact_can_be_reused(self):
        self.run_publish()
        before = self.objects.copy()

        self.run_publish()

        self.assertEqual(self.objects, before)

    def test_conflicting_artifact_is_never_overwritten(self):
        key = self.key(artifact.ARTIFACT)

        # Same size, different content.
        self.objects[key] = b"x" * len(b"test-release")

        with self.assertRaises(ValueError):
            self.run_publish()

        self.assertEqual(
            self.objects[key],
            b"x" * len(b"test-release"),
        )

        self.assertNotIn(
            self.key(artifact.MANIFEST),
            self.objects,
        )

    def test_partial_publication_can_be_resumed(self):
        key = self.key(artifact.ARTIFACT)

        self.objects[key] = (
            self.directory / artifact.ARTIFACT
        ).read_bytes()

        self.run_publish()

        self.assertEqual(len(self.objects), 3)

    def test_invalid_local_artifact_never_uploads(self):
        (self.directory / artifact.ARTIFACT).write_bytes(
            b"corrupted"
        )

        with self.assertRaises(ValueError):
            self.run_publish()

        self.assertEqual(self.uploads, [])

    def test_invalid_bucket_never_uploads(self):
        with patch("publish.aws") as aws:

            with self.assertRaises(ValueError):
                publish.publish_release(
                    "unexpected-bucket",
                    self.directory,
                    SHA,
                )

            aws.assert_not_called()


if __name__ == "__main__":
    unittest.main()
