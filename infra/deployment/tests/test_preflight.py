import stat
import sys
import tempfile
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)

import preflight

SHA = "a" * 40
UUID = "test-filesystem-uuid"

CONFIG = {
    "bucket": "gmsv5-releases-123456789012-apne1",
    "data_filesystem_uuid": UUID,
    "node_binary": "/usr/bin/node",
}


class PreflightTests(unittest.TestCase):

    def setUp(self):
        self.system = self.mock(
            "platform.system", return_value="Linux"
        )

        self.arch = self.mock(
            "platform.machine", return_value="aarch64"
        )

        self.uid = self.mock(
            "os.geteuid", return_value=0
        )

        self.config = self.mock(
            "load_config", return_value=CONFIG
        )

        self.trust = self.mock("trusted_path")

        self.disk = self.mock(
            "shutil.disk_usage",
            return_value=SimpleNamespace(
                free=10 * 1024 ** 3
            ),
        )

        self.mount = self.mock("verify_mount")

        self.current = self.mock(
            "current_release", return_value=SHA
        )

        self.runtime = "v26.9.0"
        self.working_directory = "/opt/gmsv5/current"
        self.service_user = "gmsv5"

        self.mock(
            "command", side_effect=self.fake_command
        )

    def mock(self, name, **kwargs):
        patcher = patch("preflight." + name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def fake_command(self, *args):
        if args[-1] == "--version":
            return self.runtime

        if "--property=WorkingDirectory" in args:
            return self.working_directory

        if "--property=User" in args:
            return self.service_user

        raise AssertionError(args)

    def test_valid_preflight(self):
        self.assertEqual(
            preflight.preflight(), CONFIG
        )

        self.mount.assert_called_once_with(UUID)

    def test_non_root_is_rejected(self):
        self.uid.return_value = 1000

        with self.assertRaises(ValueError):
            preflight.preflight()

        self.mount.assert_not_called()

    def test_non_linux_is_rejected(self):
        self.system.return_value = "Darwin"

        with self.assertRaises(ValueError):
            preflight.preflight()

    def test_wrong_architecture_is_rejected(self):
        self.arch.return_value = "x86_64"

        with self.assertRaises(ValueError):
            preflight.preflight()

    def test_insufficient_disk_is_rejected(self):
        self.disk.return_value = SimpleNamespace(
            free=1024
        )

        with self.assertRaises(ValueError):
            preflight.preflight()

    def test_incompatible_node_is_rejected(self):
        self.runtime = "v20.1.0"

        with self.assertRaises(ValueError):
            preflight.preflight()

    def test_mount_failure_is_rejected(self):
        self.mount.side_effect = ValueError(
            "Filesystem UUID mismatch"
        )

        with self.assertRaises(ValueError):
            preflight.preflight()

        self.current.assert_not_called()

    def test_legacy_working_directory_is_rejected(self):
        self.working_directory = (
            "/opt/gmsv5/releases/364d0b7"
        )

        with self.assertRaises(ValueError):
            preflight.preflight()

    def test_wrong_service_user_is_rejected(self):
        self.service_user = "root"

        with self.assertRaises(ValueError):
            preflight.preflight()

    def test_missing_current_release_is_rejected(self):
        self.current.side_effect = ValueError(
            "Invalid current release link"
        )

        with self.assertRaises(ValueError):
            preflight.preflight()


class MountTests(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        self.mount = Path(temp.name)
        self.database = self.mount / "gmsv5.sqlite"
        self.database.write_bytes(b"test")

        patcher = patch.multiple(
            preflight,
            MOUNT=self.mount,
            DATABASE=self.database,
        )

        patcher.start()
        self.addCleanup(patcher.stop)

    def test_matching_uuid_and_permissions(self):
        with patch(
            "preflight.command", return_value=UUID
        ), patch("preflight.subprocess.run") as run:

            preflight.verify_mount(UUID)

            self.assertEqual(run.call_count, 3)

    def test_wrong_uuid(self):
        with patch(
            "preflight.command",
            return_value="wrong-uuid",
        ):
            with self.assertRaises(ValueError):
                preflight.verify_mount(UUID)

    def test_missing_database(self):
        self.database.unlink()

        with patch(
            "preflight.command", return_value=UUID
        ):
            with self.assertRaises(ValueError):
                preflight.verify_mount(UUID)

        self.assertFalse(self.database.exists())

    def test_database_symlink_is_rejected(self):
        self.database.unlink()

        other = self.mount / "other.sqlite"
        other.write_bytes(b"test")
        self.database.symlink_to(other)

        with patch(
            "preflight.command", return_value=UUID
        ):
            with self.assertRaises(ValueError):
                preflight.verify_mount(UUID)

    def test_permission_failure(self):
        import subprocess

        with patch(
            "preflight.command", return_value=UUID
        ), patch(
            "preflight.subprocess.run",
            side_effect=subprocess.CalledProcessError(
                1, "runuser"
            ),
        ):
            with self.assertRaises(
                subprocess.CalledProcessError
            ):
                preflight.verify_mount(UUID)


class TrustedPathTests(unittest.TestCase):

    def check(self, uid=0, mode=0o100644,
              symlink=False, directory=False):

        path = MagicMock()
        path.is_symlink.return_value = symlink

        path.stat.return_value = SimpleNamespace(
            st_uid=uid,
            st_mode=mode,
        )

        with patch(
            "preflight.Path", return_value=path
        ):
            preflight.trusted_path(
                "/test/path", directory=directory
            )

    def test_root_owned_file(self):
        self.check()

    def test_non_root_owned_file(self):
        with self.assertRaises(ValueError):
            self.check(uid=1000)

    def test_world_writable_file(self):
        with self.assertRaises(ValueError):
            self.check(mode=0o100666)

    def test_symlink_is_rejected(self):
        with self.assertRaises(ValueError):
            self.check(symlink=True)

    def test_wrong_path_type(self):
        with self.assertRaises(ValueError):
            self.check(directory=True)

    def test_root_owned_directory(self):
        self.check(
            mode=stat.S_IFDIR | 0o755,
            directory=True,
        )


if __name__ == "__main__":
    unittest.main()
