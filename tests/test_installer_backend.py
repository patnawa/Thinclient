"""Safety checks in the privileged disk-install backend."""

import importlib.machinery
import importlib.util
from pathlib import Path
import types
import tempfile
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
    def test_missing_source_fails_before_erasing(self):
        with mock.patch.object(tc_install, "validate_target", return_value="/dev/sda"), \
                mock.patch.object(tc_install, "preflight", side_effect=tc_install.InstallError("missing source")), \
                mock.patch.object(tc_install, "partition") as partition:
            with self.assertRaisesRegex(tc_install.InstallError, "missing source"):
                tc_install.install("/dev/sda")
        partition.assert_not_called()

    def test_target_is_revalidated_after_preflight(self):
        with mock.patch.object(tc_install, "validate_target", side_effect=["/dev/sda", tc_install.InstallError("now mounted")]), \
                mock.patch.object(tc_install, "preflight", return_value=({}, {})), \
                mock.patch.object(tc_install, "partition") as partition:
            with self.assertRaisesRegex(tc_install.InstallError, "now mounted"):
                tc_install.install("/dev/sda")
        partition.assert_not_called()

    def test_bios_bootloader_failure_is_required(self):
        def commands(argv, check=True, **kwargs):
            if "--target=i386-pc" in argv and check:
                raise tc_install.InstallError("BIOS failed")
            return completed()
        with mock.patch.object(tc_install, "run", side_effect=commands), \
                mock.patch.object(tc_install.os.path, "exists", return_value=False):
            with self.assertRaisesRegex(tc_install.InstallError, "BIOS failed"):
                tc_install.install_bootloader("/dev/sda", "/unused")

    def test_partition_waits_for_usable_devices_not_stale_nodes(self):
        probes = []
        def run(arguments, **_kwargs):
            if arguments[0] == "blockdev":
                probes.append(arguments[-1])
                return completed("1048576" if len(probes) > 2 else "", 0 if len(probes) > 2 else 1)
            return completed()
        with mock.patch.object(tc_install, "run", side_effect=run) as commands, \
                mock.patch.object(tc_install.time, "sleep") as sleep:
            tc_install.partition("/dev/vda")
        sleep.assert_called_once_with(0.5)
        commands.assert_any_call(["udevadm", "settle", "--timeout=10"], quiet=True)
        self.assertEqual(["/dev/vda2", "/dev/vda3"] * 2, probes)

    def test_unusable_partition_nodes_fail_before_formatting(self):
        with mock.patch.object(tc_install, "run", return_value=completed("", 1)), \
                mock.patch.object(tc_install.time, "sleep"), \
                mock.patch.object(tc_install, "format_partitions") as formatting:
            with self.assertRaisesRegex(tc_install.InstallError, "did not appear"):
                tc_install.partition("/dev/vda")
        formatting.assert_not_called()

    def test_target_is_validated_before_the_first_destructive_step(self):
        with mock.patch.object(
                tc_install, "validate_target",
                side_effect=tc_install.InstallError("unsafe target")), \
                mock.patch.object(tc_install, "partition") as partition:
            with self.assertRaisesRegex(tc_install.InstallError, "unsafe target"):
                tc_install.install("/dev/sda1")
        partition.assert_not_called()


class SeedConfig(unittest.TestCase):
    def test_configuration_failure_is_fatal(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(tc_install.tempfile, "mkdtemp", return_value=directory), \
                mock.patch.object(tc_install, "run", side_effect=tc_install.InstallError("mount failed")), \
                mock.patch.object(tc_install.os, "rmdir"):
            with self.assertRaisesRegex(tc_install.InstallError, "could not seed"):
                tc_install.seed_config("/dev/sda", {})

    def test_internal_install_carries_public_authorization_not_host_keys(self):
        fake_tcconfig = types.SimpleNamespace(load=lambda: {
            "device": {}, "connections": [],
        })
        with tempfile.TemporaryDirectory() as destination, \
                mock.patch.object(tc_install.tempfile, "mkdtemp",
                                  return_value=destination), \
                mock.patch.object(tc_install, "run", return_value=completed()), \
                mock.patch.dict(tc_install.sys.modules, {"tcconfig": fake_tcconfig}), \
                mock.patch.object(tc_install.os.path, "isfile", return_value=True), \
                mock.patch.object(tc_install.os.path, "islink", return_value=False), \
                mock.patch.object(tc_install.shutil, "copyfile") as copyfile:
            tc_install.seed_config("/dev/sda")

        copyfile.assert_called_once_with(
            "/run/thinclient-support/user/authorized_keys",
            str(Path(destination) / "support" / "authorized_keys"),
        )


class SourceVerification(unittest.TestCase):
    def test_preflight_rejects_missing_tool(self):
        with mock.patch.object(tc_install.shutil, "which", return_value=None):
            with self.assertRaisesRegex(tc_install.InstallError, "tool is missing"):
                tc_install.preflight()

    def test_preflight_rejects_missing_payload(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(tc_install, "LIVE_MEDIUM", directory), \
                mock.patch.object(tc_install.shutil, "which", return_value="/mock/tool"), \
                mock.patch.object(tc_install.os.path, "isdir", return_value=True):
            with self.assertRaisesRegex(tc_install.InstallError, "missing or empty"):
                tc_install.preflight()

    def test_copy_rejects_changed_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "medium" / "live"
            source.mkdir(parents=True)
            for name in tc_install.SYSTEM_FILES:
                (source / name).write_bytes(b"valid bytes")
            expected = {name: tc_install.file_hash(source / name) for name in tc_install.SYSTEM_FILES}
            (source / "initrd.img").write_bytes(b"changed bytes")
            with mock.patch.object(tc_install, "LIVE_MEDIUM", str(source.parent)):
                with self.assertRaisesRegex(tc_install.InstallError, "initrd.img failed verification"):
                    tc_install.copy_system("/unused", str(Path(directory) / "target"), expected)

    def test_successful_copy_matches_preflight_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "medium" / "live"
            source.mkdir(parents=True)
            for name in tc_install.SYSTEM_FILES:
                (source / name).write_bytes(name.encode())
            expected = {name: tc_install.file_hash(source / name) for name in tc_install.SYSTEM_FILES}
            with mock.patch.object(tc_install, "LIVE_MEDIUM", str(source.parent)):
                tc_install.copy_system("/unused", str(Path(directory) / "target"), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
