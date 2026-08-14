"""Runtime contract for the removable USB cache writer."""

import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
import unittest


SCRIPT = (Path(__file__).resolve().parents[1] /
          "overlay/usr/local/sbin/tc-cache-save")


@unittest.skipUnless(os.name == "posix", "requires a POSIX shell")
class CacheSave(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="thinclient-cache-save-"))
        self.addCleanup(shutil.rmtree, self.temp)
        self.bin = self.temp / "bin"
        self.bin.mkdir()
        self.live = self.temp / "live-medium"
        (self.live / "live").mkdir(parents=True)
        self.mount = self.temp / "cache-media"
        self.mount.mkdir()
        self.run = self.temp / "run"
        self.run.mkdir()
        self.run.chmod(0o1775)
        self.source = self.live / "live/filesystem.squashfs"
        self.source.write_bytes((b"verified-root-image\n" * 4096))
        self.digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.cmdline = self.temp / "cmdline"
        self.cmdline.write_text(
            "tc.cache=1 tc.cache.profile=lite tc.cache.label=TCCACHE "
            "tc.cache.sha256=%s fetch=http://pxe/thinclient/lite/filesystem.squashfs\n"
            % self.digest,
            encoding="utf-8",
        )
        self.init_status = self.run / "init-status"
        self.init_status.write_text("state=network\nprofile=lite\n", encoding="utf-8")

        self._program("blkid", """#!/bin/sh
case "$*" in
  *"-t LABEL=TCCACHE -o device"*) printf '/dev/fakeusb\\n' ;;
  *"-s TYPE -o value /dev/fakeusb"*) printf 'ext4\\n' ;;
  *) exit 1 ;;
esac
""")
        self._program("udevadm", """#!/bin/sh
case "$1" in
  info) printf 'ID_BUS=usb\\n' ;;
  settle) exit 0 ;;
esac
""")
        for name in ("mount", "umount", "sync"):
            self._program(name, "#!/bin/sh\nexit 0\n")
        # Keep the progress file observable instead of finishing between polls.
        self._program("tee", "#!/bin/sh\nsleep 2\nexec /usr/bin/tee \"$@\"\n")

    def _program(self, name, text):
        path = self.bin / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def test_progress_is_atomic_and_success_is_verified(self):
        progress = self.run / "cache-progress"
        saved = self.run / "cache-status"
        victim = self.temp / "must-not-be-overwritten"
        victim.write_text("protected\n", encoding="utf-8")
        saved.symlink_to(victim)
        env = os.environ.copy()
        env.update({
            "PATH": "%s:/usr/bin:/bin" % self.bin,
            "TC_CACHE_CMDLINE_FILE": str(self.cmdline),
            "TC_CACHE_STATUS_FILE": str(self.init_status),
            "TC_CACHE_LIVE_MEDIUM": str(self.live),
            "TC_CACHE_MOUNT_DIR": str(self.mount),
            "TC_CACHE_SAVE_STATUS_FILE": str(saved),
            "TC_CACHE_PROGRESS_FILE": str(progress),
        })
        process = subprocess.Popen(
            ["/bin/sh", str(SCRIPT)], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        deadline = time.monotonic() + 4
        while not progress.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(progress.exists(), "cache writer never published progress")
        progress_text = progress.read_text(encoding="utf-8")
        self.assertIn("state=saving\n", progress_text)
        self.assertIn("profile=lite\n", progress_text)
        self.assertIn("device=/dev/fakeusb\n", progress_text)

        output, _ = process.communicate(timeout=10)
        self.assertEqual(0, process.returncode, output)
        target = self.mount / ("thinclient-cache/lite/%s.squashfs" % self.digest)
        self.assertEqual(self.source.read_bytes(), target.read_bytes())
        self.assertFalse(progress.exists(), "finished progress must be removed")
        self.assertFalse(saved.is_symlink(), "status symlink must be replaced, not followed")
        self.assertEqual("protected\n", victim.read_text(encoding="utf-8"))
        saved_text = saved.read_text(encoding="utf-8")
        self.assertIn("state=saved\n", saved_text)
        self.assertIn("profile=lite\n", saved_text)
        self.assertIn("sha256=%s\n" % self.digest, saved_text)
        self.assertIn("device=/dev/fakeusb\n", saved_text)
        self.assertFalse(any(self.mount.rglob("*.part.*")))
        self.assertEqual(0o1775, stat.S_IMODE(self.run.stat().st_mode))


if __name__ == "__main__":
    unittest.main(verbosity=2)
