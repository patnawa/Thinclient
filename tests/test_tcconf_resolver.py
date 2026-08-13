"""Device-selection tests for the duplicated TCCONF shell resolver."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


REPO = Path(__file__).resolve().parents[1]
SCRIPT_DIR = (REPO / "overlay/usr/local/sbin"
              if (REPO / "overlay/usr/local/sbin").is_dir()
              else Path("/usr/local/sbin"))
SCRIPTS = [SCRIPT_DIR / "tc-fetch-config", SCRIPT_DIR / "tc-save-config"]
BEGIN = "# BEGIN TCCONF_DEVICE_RESOLVER"
END = "# END TCCONF_DEVICE_RESOLVER"


def resolver_block(path):
    text = path.read_text(encoding="utf-8")
    start = text.index(BEGIN)
    finish = text.index(END, start) + len(END)
    return text[start:finish]


@unittest.skipUnless(os.name == "posix" and shutil.which("sh"),
                     "requires a POSIX shell")
class TcconfResolver(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blocks = [resolver_block(path) for path in SCRIPTS]

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.bin = Path(self.tempdir.name) / "bin"
        self.bin.mkdir()
        self._mock_command("findmnt", r"""
            case "$*" in
                *"--target /run/thinclient/media"*)
                    [ -z "${MOCK_MEDIA_SOURCE:-}" ] || printf '%s\n' "$MOCK_MEDIA_SOURCE"
                    ;;
                *"--target /run/live/medium"*)
                    [ -z "${MOCK_BOOT_SOURCE:-}" ] || printf '%s\n' "$MOCK_BOOT_SOURCE"
                    ;;
            esac
        """)
        self._mock_command("lsblk", r"""
            for argument do :; done
            case "$argument" in
                /dev/sdb1|/dev/sdb2) printf 'sdb\n' ;;
                /dev/sdc1|/dev/sdc2) printf 'sdc\n' ;;
                /dev/sdd1|/dev/sdd2) printf 'sdd\n' ;;
            esac
        """)
        self._mock_command("blkid", r"""
            if [ "${1:-}" = "-t" ]; then
                printf '%s\n' "${MOCK_CANDIDATES:-}"
                exit 0
            fi
            case "$*" in
                *"-s TYPE"*) printf '%s\n' "${MOCK_FSTYPE:-vfat}"; exit 0 ;;
            esac
            for argument do :; done
            case "$argument" in
                /dev/sdb2|/dev/sdc2|/dev/sdd2) printf 'TCCONF\n' ;;
                *) printf 'OTHER\n' ;;
            esac
        """)
        self._mock_command("readlink", r"""
            for argument do :; done
            printf '%s\n' "$argument"
        """)

    def tearDown(self):
        self.tempdir.cleanup()

    def _mock_command(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + textwrap.dedent(body), encoding="utf-8")
        path.chmod(0o755)

    def _run(self, command, **values):
        environment = os.environ.copy()
        environment.update({key: value for key, value in values.items()})
        environment["PATH"] = f"{self.bin}:{environment['PATH']}"
        script = self.blocks[0] + "\n" + command + "\n"
        return subprocess.run(
            ["sh", "-c", script],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

    def test_fetch_and_save_use_the_exact_same_resolver(self):
        self.assertEqual(self.blocks[0], self.blocks[1])

    def test_valid_mounted_partition_is_authoritative(self):
        result = self._run(
            "tcconf_mounted_device /run/thinclient/media",
            MOCK_MEDIA_SOURCE="/dev/sdc2",
            MOCK_BOOT_SOURCE="/dev/sdb1",
            MOCK_CANDIDATES="/dev/sdb2",
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("/dev/sdc2", result.stdout.strip())

    def test_non_tcconf_mount_is_rejected(self):
        result = self._run(
            "tcconf_mounted_device /run/thinclient/media",
            MOCK_MEDIA_SOURCE="/dev/notconf",
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("", result.stdout)

    def test_partition_on_live_boot_disk_wins_over_first_duplicate(self):
        result = self._run(
            "tcconf_discover_device",
            MOCK_BOOT_SOURCE="/dev/sdb1",
            MOCK_CANDIDATES="/dev/sdc2\n/dev/sdb2",
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("/dev/sdb2", result.stdout.strip())

    def test_first_blkid_result_is_deterministic_fallback(self):
        result = self._run(
            "tcconf_discover_device",
            MOCK_BOOT_SOURCE="/dev/sdd1",
            MOCK_CANDIDATES="/dev/sdc2\n/dev/sdb2",
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("/dev/sdc2", result.stdout.strip())

    def test_no_labelled_partition_returns_failure(self):
        result = self._run("tcconf_discover_device", MOCK_CANDIDATES="")

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("", result.stdout)

    def test_fat_mounts_are_root_only_and_non_executable(self):
        result = self._run(
            "tcconf_mount_options rw /dev/sdb2",
            MOCK_FSTYPE="vfat",
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "rw,noatime,nosuid,nodev,noexec,uid=0,gid=0,fmask=0177,dmask=0077",
            result.stdout.strip(),
        )

    def test_non_fat_label_is_rejected(self):
        result = self._run(
            "tcconf_labelled_device /dev/sdb2",
            MOCK_FSTYPE="ext4",
        )

        self.assertNotEqual(0, result.returncode)


if __name__ == "__main__":
    unittest.main()
