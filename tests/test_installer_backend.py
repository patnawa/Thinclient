"""Safety checks in the privileged disk-install backend."""

import importlib.machinery
import importlib.util
from pathlib import Path
import types
import unittest
from unittest import mock


REPO_SCRIPT = (Path(__file__).resolve().parents[1] /
               "overlay/usr/local/sbin/tc-install")
SCRIPT = REPO_SCRIPT if REPO_SCRIPT.is_file() else Path("/usr/local/sbin/tc-install")
LOADER = importlib.machinery.SourceFileLoader("tc_install", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
tc_install = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(tc_install)


def completed(stdout="", returncode=0):
    return types.SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


class ValidateTarget(unittest.TestCase):
    def candidate(self, **overrides):
        disk = {
            "path": "/dev/sda", "size": 16 * 1024 ** 3,
            "too_small": False,
        }
        disk.update(overrides)
        return disk

    def validate(self, target="/dev/sda", candidates=None, mounts="disk\n"):
        candidates = candidates if candidates is not None else [self.candidate()]
        with mock.patch.object(tc_install.os.path, "exists", return_value=True), \
                mock.patch.object(tc_install.os.path, "realpath", side_effect=lambda p: p), \
                mock.patch.object(tc_install, "candidates", return_value=candidates), \
                mock.patch.object(tc_install, "run", return_value=completed(mounts)):
            return tc_install.validate_target(target)

    def test_accepts_an_unmounted_candidate_whole_disk(self):
        self.assertEqual("/dev/sda", self.validate())

    def test_rejects_a_partition_even_when_it_exists(self):
        with self.assertRaisesRegex(tc_install.InstallError, "whole disk"):
            self.validate("/dev/sda1")

    def test_rejects_a_disk_below_the_minimum_size(self):
        small = self.candidate(size=2 * 1024 ** 3, too_small=True)
        with self.assertRaisesRegex(tc_install.InstallError, "too small"):
            self.validate(candidates=[small])

    def test_rejects_a_disk_with_any_mounted_child(self):
        with self.assertRaisesRegex(tc_install.InstallError, "mounted filesystem"):
            self.validate(mounts="disk\npart /mnt/data\n")

    def test_refuses_to_continue_when_mount_recheck_fails(self):
        with mock.patch.object(tc_install.os.path, "exists", return_value=True), \
                mock.patch.object(tc_install.os.path, "realpath", side_effect=lambda p: p), \
                mock.patch.object(tc_install, "candidates", return_value=[self.candidate()]), \
                mock.patch.object(tc_install, "run", return_value=completed(returncode=1)):
            with self.assertRaisesRegex(tc_install.InstallError, "could not re-check"):
                tc_install.validate_target("/dev/sda")


class InstallOrdering(unittest.TestCase):
    def test_target_is_validated_before_the_first_destructive_step(self):
        with mock.patch.object(
                tc_install, "validate_target",
                side_effect=tc_install.InstallError("unsafe target")), \
                mock.patch.object(tc_install, "partition") as partition:
            with self.assertRaisesRegex(tc_install.InstallError, "unsafe target"):
                tc_install.install("/dev/sda1")
        partition.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
