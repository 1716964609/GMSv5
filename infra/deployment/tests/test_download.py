import hashlib
import json
import sys
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)

import artifact
import download

SHA = "a" * 40
BUCKET = "gmsv5-releases-123456789012-apne1"


class DownloadTests(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        self.directory = Path(temp.name)

        data = b"test artifact"
        digest = hashlib.sha256(data).hexdigest()

        self.objects = {
            artifact.ARTIFACT: data,
            artifact.CHECKSUM: (
                f"{digest}  {artifact.ARTIFACT}\n"
            ).encode(),
            artifact.MANIFEST: json.dumps({
                "manifest_version": 1,
                "commit_sha": SHA,
                "artifact": artifact.ARTIFACT,
                "artifact_sha256": digest,
                "architecture": "arm64",
                "os": "linux",
                "node_version": "v26.9.0",
                "database_schema_fingerprint": "c" * 64,
                "workflow_run_id": "123",
                "workflow_run_attempt": "1",
            }).encode(),
        }

    def fake_aws(self, *args):
        key = args[args.index("--key") + 1]
        filename = key.rsplit("/", 1)[1]
        data = self.objects[filename]

        self.assertEqual(
            args[args.index("--bucket") + 1],
            BUCKET,
        )

        self.assertEqual(
            key,
            f"releases/{SHA}/{filename}",
        )

        if args[0] == "head-object":
            return json.dumps({
                "ContentLength": len(data),
                "ETag": '"test-etag"',
            })

        if args[0] == "get-object":
            self.assertIn("--if-match", args)
            self.assertIn('"test-etag"', args)

            Path(args[-1]).write_bytes(data)
            return "{}"

        raise AssertionError("Unexpected AWS operation")

    def run_download(self):
        with patch(
            "download.aws",
            side_effect=self.fake_aws,
        ):
            return download.download_release(
                BUCKET, SHA, self.directory
            )

    def test_valid_download(self):
        result = self.run_download()

        self.assertEqual(result["commit_sha"], SHA)

    def test_corrupted_download_is_rejected(self):
        self.objects[artifact.ARTIFACT] = b"corrupted"

        with self.assertRaises(ValueError):
            self.run_download()

    def test_invalid_bucket_is_rejected(self):
        with patch("download.aws") as aws:
            with self.assertRaises(ValueError):
                download.download_release(
                    "unexpected-bucket",
                    SHA,
                    self.directory,
                )

            aws.assert_not_called()

    def test_invalid_sha_is_rejected(self):
        with patch("download.aws") as aws:
            with self.assertRaises(ValueError):
                download.download_release(
                    BUCKET,
                    "../invalid",
                    self.directory,
                )

            aws.assert_not_called()

    def test_nonempty_directory_is_rejected(self):
        (self.directory / "existing").write_text("test")

        with self.assertRaises(ValueError):
            self.run_download()

    def test_oversized_object_is_rejected(self):
        self.objects[artifact.MANIFEST] = b"x" * 16385

        with self.assertRaises(ValueError):
            self.run_download()

    def test_missing_object_is_rejected(self):
        del self.objects[artifact.CHECKSUM]

        with self.assertRaises(KeyError):
            self.run_download()


if __name__ == "__main__":
    unittest.main()
