import io
import json
import sys
import tarfile
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)

import artifact
import stage

OLD = "a" * 40
NEW = "b" * 40


class StageTests(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        self.root = Path(temp.name)
        self.source = self.root / "source"
        self.source.mkdir()

        self.releases = self.root / "releases"
        self.releases.mkdir()

        self.old = self.releases / OLD
        self.old.mkdir()
        (self.old / "server.js").write_text("old")

        self.current = self.root / "current"

        self.current.symlink_to("releases/" + OLD)

        self.target = self.releases / NEW

        self.archive = self.source / artifact.ARTIFACT

        self.make_archive()
        self.refresh_manifest()

        patcher = patch(
            "stage.MIN_FREE_BYTES", 0
        )

        patcher.start()
        self.addCleanup(patcher.stop)

    def make_archive(self, valid=True):
        with tarfile.open(self.archive, "w:gz") as tar:
            if valid:
                server = tarfile.TarInfo("server.js")
                server.size = 3

                tar.addfile(
                    server,
                    io.BytesIO(b"new"),
                )

            directory = tarfile.TarInfo(".next")
            directory.type = tarfile.DIRTYPE
            tar.addfile(directory)

    def refresh_manifest(self):
        digest = artifact.sha256(self.archive)

        (self.source / artifact.CHECKSUM).write_text(
            f"{digest}  {artifact.ARTIFACT}\n"
        )

        self.manifest = {
            "manifest_version": 1,
            "commit_sha": NEW,
            "artifact": artifact.ARTIFACT,
            "artifact_sha256": digest,
            "architecture": "arm64",
            "os": "linux",
            "node_version": "v26.9.0",
            "database_schema_fingerprint": "c" * 64,
            "workflow_run_id": "12345",
            "workflow_run_attempt": "1",
        }

        (self.source / artifact.MANIFEST).write_text(
            json.dumps(self.manifest)
        )

    def stage(self):
        return stage.stage_release(
            self.root, self.source, NEW
        )

    def assert_current_unchanged(self):
        self.assertEqual(
            self.current.resolve(), self.old.resolve()
        )

        self.assertEqual(
            (self.old / "server.js").read_text(),
            "old",
        )

    def test_successful_staging(self):
        self.assertEqual(self.stage(), self.target)

        self.assertEqual(
            (self.target / "server.js").read_text(),
            "new",
        )

        self.assertEqual(
            json.loads(
                (self.target / "manifest.json").read_text()
            ),
            self.manifest,
        )

        self.assert_current_unchanged()

    def test_identical_release_is_idempotent(self):
        self.stage()
        before = self.target.stat().st_ino

        self.stage()

        self.assertEqual(
            self.target.stat().st_ino, before
        )

        self.assert_current_unchanged()

    def test_existing_release_mismatch_is_rejected(self):
        self.stage()

        path = self.target / "manifest.json"
        path.write_text("{}")

        with self.assertRaises(ValueError):
            self.stage()

        self.assertEqual(path.read_text(), "{}")
        self.assert_current_unchanged()

    def test_corrupted_archive_is_rejected(self):
        self.archive.write_bytes(b"corrupted")

        with self.assertRaises(ValueError):
            self.stage()

        self.assertFalse(self.target.exists())
        self.assert_current_unchanged()

    def test_invalid_archive_is_never_published(self):
        self.make_archive(valid=False)
        self.refresh_manifest()

        with self.assertRaises(ValueError):
            self.stage()

        self.assertFalse(self.target.exists())

        self.assertEqual(
            list(self.releases.glob(".staging-*")),
            [],
        )

        self.assert_current_unchanged()

    def test_insufficient_disk_space(self):
        with patch(
            "stage.MIN_FREE_BYTES", 10 ** 30
        ):
            with self.assertRaises(ValueError):
                self.stage()

        self.assertFalse(self.target.exists())
        self.assert_current_unchanged()

    def test_sync_failure_does_not_publish_release(self):
        with patch(
            "stage.sync_tree",
            side_effect=OSError("simulated disk error"),
        ):
            with self.assertRaises(OSError):
                self.stage()

        self.assertFalse(self.target.exists())
        self.assert_current_unchanged()

    def test_existing_release_symlink_is_rejected(self):
        self.target.symlink_to(self.old)

        with self.assertRaises(ValueError):
            self.stage()

        self.assert_current_unchanged()


if __name__ == "__main__":
    unittest.main()
