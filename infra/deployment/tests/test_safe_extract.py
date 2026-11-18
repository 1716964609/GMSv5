import io
import os
import sys
import tarfile
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)

import safe_extract


class ExtractionTests(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        self.root = Path(temp.name)
        self.archive = self.root / "release.tar.gz"
        self.destination = self.root / "release"

    def make_archive(self, extra=()):
        with tarfile.open(self.archive, "w:gz") as tar:
            server = tarfile.TarInfo("server.js")
            server.size = 4
            tar.addfile(server, io.BytesIO(b"test"))

            directory = tarfile.TarInfo(".next")
            directory.type = tarfile.DIRTYPE
            tar.addfile(directory)

            for name, kind, link in extra:
                entry = tarfile.TarInfo(name)
                entry.type = kind
                entry.linkname = link

                tar.addfile(entry)

    def extract(self):
        safe_extract.extract_archive(
            self.archive, self.destination
        )

    def reject(self):
        with self.assertRaises((ValueError, OSError)):
            self.extract()

        self.assertFalse(self.destination.exists())

    def test_valid_archive(self):
        self.make_archive()
        self.extract()

        self.assertEqual(
            (self.destination / "server.js").read_bytes(),
            b"test",
        )

    def test_restrictive_umask(self):
        self.make_archive([
            ("node_modules/next/package.json", tarfile.REGTYPE, ""),
            ("bin/server", tarfile.SYMTYPE, "../server.js"),
        ])

        previous = os.umask(0o077)

        try:
            self.extract()
        finally:
            os.umask(previous)

        for directory in (
            self.destination,
            self.destination / ".next",
            self.destination / "node_modules",
            self.destination / "node_modules/next",
            self.destination / "bin",
        ):
            self.assertEqual(
                directory.stat().st_mode & 0o777,
                0o755,
            )

        self.assertEqual(
            (self.destination / "node_modules/next/package.json")
            .stat().st_mode & 0o777,
            0o644,
        )

        self.assertTrue(
            (self.destination / "bin/server").is_symlink()
        )

    def test_path_traversal(self):
        self.make_archive([
            ("../escaped", tarfile.REGTYPE, "")
        ])

        self.reject()
        self.assertFalse(
            (self.root / "escaped").exists()
        )

    def test_absolute_path(self):
        self.make_archive([
            ("/tmp/escaped", tarfile.REGTYPE, "")
        ])

        self.reject()

    def test_hardlink(self):
        self.make_archive([
            ("hardlink", tarfile.LNKTYPE, "server.js")
        ])

        self.reject()

    def test_special_file(self):
        self.make_archive([
            ("fifo", tarfile.FIFOTYPE, "")
        ])

        self.reject()

    def test_duplicate_entry(self):
        self.make_archive([
            ("server.js", tarfile.REGTYPE, "")
        ])

        self.reject()

    def test_external_symlink(self):
        (self.root / "outside").write_text("outside")

        self.make_archive([
            ("link", tarfile.SYMTYPE, "../outside")
        ])

        self.reject()

    def test_absolute_symlink(self):
        self.make_archive([
            ("link", tarfile.SYMTYPE, "/etc/passwd")
        ])

        self.reject()

    def test_write_below_symlink(self):
        self.make_archive([
            ("link", tarfile.SYMTYPE, ".next"),
            ("link/file", tarfile.REGTYPE, ""),
        ])

        self.reject()

    def test_internal_symlink(self):
        self.make_archive([
            ("bin/server", tarfile.SYMTYPE, "../server.js")
        ])

        self.extract()

        self.assertEqual(
            (self.destination / "bin/server").read_bytes(),
            b"test",
        )

    def test_expanded_size_limit(self):
        self.make_archive()

        with patch("safe_extract.MAX_BYTES", 3):
            self.reject()

    def test_existing_release_is_preserved(self):
        self.make_archive()
        self.destination.mkdir()

        marker = self.destination / "existing"
        marker.write_text("preserve")

        with self.assertRaises(FileExistsError):
            self.extract()

        self.assertEqual(marker.read_text(), "preserve")

    def test_missing_server(self):
        with tarfile.open(self.archive, "w:gz"):
            pass

        self.reject()


if __name__ == "__main__":
    unittest.main()
