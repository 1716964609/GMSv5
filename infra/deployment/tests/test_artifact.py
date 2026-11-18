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

SHA = "a" * 40


class ArtifactTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

        self.archive = self.directory / artifact.ARTIFACT
        self.archive.write_bytes(b"test-release-artifact")

        self.digest = artifact.sha256(self.archive)

        (self.directory / artifact.CHECKSUM).write_text(
            f"{self.digest}  {artifact.ARTIFACT}\n"
        )

        self.manifest = {
            "manifest_version": 1,
            "commit_sha": SHA,
            "artifact": artifact.ARTIFACT,
            "artifact_sha256": self.digest,
            "architecture": "arm64",
            "os": "linux",
            "node_version": "v26.9.0",
            "database_schema_fingerprint": "c" * 64,
            "workflow_run_id": "12345",
            "workflow_run_attempt": "1",
        }

        self.write_manifest()

    def write_manifest(self):
        (self.directory / artifact.MANIFEST).write_text(
            json.dumps(self.manifest)
        )

    def verify(self):
        return artifact.verify_manifest(
            self.directory, SHA
        )

    def test_valid_artifact(self):
        self.assertEqual(
            self.verify()["commit_sha"], SHA
        )

    def test_corrupted_artifact(self):
        self.archive.write_bytes(b"corrupted")

        with self.assertRaises(ValueError):
            self.verify()

    def test_wrong_commit(self):
        self.manifest["commit_sha"] = "b" * 40
        self.write_manifest()

        with self.assertRaises(ValueError):
            self.verify()

    def test_wrong_architecture(self):
        self.manifest["architecture"] = "amd64"
        self.write_manifest()

        with self.assertRaises(ValueError):
            self.verify()

    def test_wrong_operating_system(self):
        self.manifest["os"] = "darwin"
        self.write_manifest()

        with self.assertRaises(ValueError):
            self.verify()

    def test_wrong_node_version(self):
        self.manifest["node_version"] = "v20.1.0"
        self.write_manifest()

        with self.assertRaises(ValueError):
            self.verify()

    def test_wrong_manifest_checksum(self):
        self.manifest["artifact_sha256"] = "f" * 64
        self.write_manifest()

        with self.assertRaises(ValueError):
            self.verify()

    def test_missing_manifest(self):
        (self.directory / artifact.MANIFEST).unlink()

        with self.assertRaises(ValueError):
            self.verify()

    def test_missing_checksum(self):
        (self.directory / artifact.CHECKSUM).unlink()

        with self.assertRaises(ValueError):
            self.verify()

    def test_invalid_schema_fingerprint(self):
        self.manifest["database_schema_fingerprint"] = "invalid"
        self.write_manifest()

        with self.assertRaises(ValueError):
            self.verify()

    def test_unexpected_artifact_filename(self):
        self.manifest["artifact"] = "../../server.js"
        self.write_manifest()

        with self.assertRaises(ValueError):
            self.verify()

    def test_unsupported_manifest_version(self):
        self.manifest["manifest_version"] = 999
        self.write_manifest()

        with self.assertRaises(ValueError):
            self.verify()

    def test_non_linux_build_is_rejected(self):
        with patch("artifact.platform.system",
                   return_value="Darwin"):
            with self.assertRaises(ValueError):
                artifact.create_manifest(
                    self.directory, SHA
                )

    def test_create_manifest_on_mocked_arm64_runner(self):
        (self.directory / artifact.MANIFEST).unlink()

        with patch("artifact.platform.system",
                   return_value="Linux"), \
             patch("artifact.platform.machine",
                   return_value="aarch64"), \
             patch("artifact.subprocess.check_output",
                   return_value="v26.9.0\n"), \
             patch.dict("os.environ", {
                 "GITHUB_RUN_ID": "12345",
                 "GITHUB_RUN_ATTEMPT": "1",
             }):

            result = artifact.create_manifest(
                self.directory, SHA
            )

        self.assertEqual(
            result["artifact_sha256"], self.digest
        )

        self.assertEqual(
            result["database_schema_fingerprint"],
            artifact.sha256(artifact.MIGRATIONS),
        )


if __name__ == "__main__":
    unittest.main()
