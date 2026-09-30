"""Runtime contract for the removable USB cache writer."""

import hashlib
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unittest


REPO_SCRIPT = (Path(__file__).resolve().parents[1] /
               "overlay/usr/local/sbin/tc-cache-save")
SCRIPT = REPO_SCRIPT if REPO_SCRIPT.is_file() else Path("/usr/local/sbin/tc-cache-save")


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

    def environment(self):
        return dict(os.environ, PATH="%s:/usr/bin:/bin" % self.bin,
                    TC_CACHE_CMDLINE_FILE=str(self.cmdline),
                    TC_CACHE_STATUS_FILE=str(self.init_status),
                    TC_CACHE_LIVE_MEDIUM=str(self.live),
                    TC_CACHE_MOUNT_DIR=str(self.mount),
                    TC_CACHE_SAVE_STATUS_FILE=str(self.run / "cache-status"),
                    TC_CACHE_BOOT_STATUS_FILE=str(self.run / "cache-boot-status"),
                    TC_CACHE_PROGRESS_FILE=str(self.run / "cache-progress"))

    def test_cache_hit_publishes_readable_boot_status_without_copying(self):
        self.init_status.write_text("state=hit\nprofile=lite\n")
        self.source.unlink()
        result = subprocess.run(["/bin/sh", str(SCRIPT)], env=self.environment(),
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        status = self.run / "cache-boot-status"
        self.assertEqual("state=hit\nprofile=lite\n", status.read_text())
        self.assertEqual(0o644, stat.S_IMODE(status.stat().st_mode))

    def assert_failed_write_preserves_cache(self, writer, sync=None):
        directory = self.mount / "thinclient-cache/lite"
        directory.mkdir(parents=True)
        previous = directory / ("a" * 64 + ".squashfs")
        previous.write_bytes(b"previous verified image")
        self._program("tee", writer)
        if sync:
            self._program("sync", sync)
        result = subprocess.run(["/bin/sh", str(SCRIPT)], env=self.environment(),
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(0, result.returncode, result.stdout)
        self.assertEqual(b"previous verified image", previous.read_bytes())
        self.assertFalse((directory / (self.digest + ".squashfs")).exists())
        self.assertFalse(any(directory.glob("*.part.*")))
        self.assertIn("state=failed", (self.run / "cache-status").read_text())
        self.assertFalse((self.run / "cache-progress").exists())

    def test_full_device_does_not_publish_input_hash_as_verified_cache(self):
        self.assert_failed_write_preserves_cache(
            '#!/bin/sh\nexec /usr/bin/tee /dev/full\n')

    def test_truncated_write_with_success_exit_is_rejected(self):
        self.assert_failed_write_preserves_cache(
            '#!/bin/sh\nprintf truncated > "$1"\ncat >/dev/null\n')

    def test_disconnected_device_is_rejected(self):
        self.assert_failed_write_preserves_cache(
            '#!/bin/sh\ncat >/dev/null\nrm -f -- "$1"\nexit 1\n')

    def test_flush_failure_preserves_previous_cache(self):
        self.assert_failed_write_preserves_cache(
            '#!/bin/sh\nexec /usr/bin/tee "$@"\n', '#!/bin/sh\nexit 1\n')

    def test_termination_cleans_partial_cache(self):
        # Terminate during a slow write, without mounting or modifying a device.
        self._program("tee", '#!/bin/sh\nexec sleep 30\n')
        process = subprocess.Popen(["/bin/sh", str(SCRIPT)], env=self.environment(),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 3
            while not (self.run / "cache-progress").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue((self.run / "cache-progress").exists())
            process.terminate()
            process.communicate(timeout=5)
            self.assertNotEqual(0, process.returncode)
            self.assertFalse(any(self.mount.rglob("*.part.*")))
            self.assertIn("interrupted", (self.run / "cache-status").read_text())
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_cancellation_before_copy_pid_is_registered_reaps_writer(self):
        # Force the scheduler boundary between spawning the copy and recording
        # its PID. Otherwise the real cancellation race only fails under load.
        source = SCRIPT.read_text(encoding="utf-8")
        registration = "    COPY_PID=$!\n"
        self.assertEqual(1, source.count(registration))
        paused_script = self.temp / "paused-cache-save"
        paused_script.write_text(source.replace(
            registration, '    kill -STOP "$$"\n' + registration), encoding="utf-8")
        writer_pid_file = self.run / "writer-pid"
        self._program("tee", '#!/bin/sh\nprintf "%s\\n" "$$" > "$TC_TEST_WRITER_PID"\n'
                      'exec sleep 30\n')
        env = dict(self.environment(), TC_TEST_WRITER_PID=str(writer_pid_file))
        for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=sig):
                writer_pid_file.unlink(missing_ok=True)
                process = subprocess.Popen(
                    ["/bin/sh", str(paused_script)], env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    start_new_session=True,
                )
                try:
                    deadline = time.monotonic() + 3
                    stopped = False
                    while time.monotonic() < deadline:
                        pid, status = os.waitpid(process.pid, os.WNOHANG | os.WUNTRACED)
                        if pid and os.WIFSTOPPED(status):
                            stopped = True
                        if stopped and writer_pid_file.exists():
                            break
                        time.sleep(0.01)
                    self.assertTrue(stopped, "writer never reached the registration boundary")
                    self.assertTrue(writer_pid_file.exists(), "copy process did not start")
                    writer_pid = int(writer_pid_file.read_text())
                    process.send_signal(sig)
                    process.send_signal(signal.SIGCONT)
                    process.communicate(timeout=5)
                    self.assertEqual(128 + sig, process.returncode)
                    with self.assertRaises(ProcessLookupError):
                        os.kill(writer_pid, 0)
                    self.assertFalse(any(self.mount.rglob("*.part.*")))
                    self.assertFalse((self.run / "cache-progress").exists())
                    self.assertIn("interrupted", (self.run / "cache-status").read_text())
                finally:
                    # A failing regression may leave the writer alive after its
                    # parent exits; clean up only this test's private process group.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.communicate(timeout=5)

    def test_termination_reaps_writer_even_if_term_is_ignored(self):
        # Cancellation discards this copy, so cleanup must not depend on the
        # child's signal handler or its progress through shell/exec startup.
        self._program("tee", '#!/bin/sh\ntrap "" TERM\n'
                      ': > "$TC_TEST_WRITER_READY"\nexec sleep 30\n')
        ready = self.run / "writer-ready"
        env = dict(self.environment(), TC_TEST_WRITER_READY=str(ready))
        process = subprocess.Popen(
            ["/bin/sh", str(SCRIPT)], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "copy process did not start")
            process.terminate()
            process.communicate(timeout=5)
            self.assertEqual(143, process.returncode)
            self.assertFalse(any(self.mount.rglob("*.part.*")))
            self.assertFalse((self.run / "cache-progress").exists())
            self.assertIn("interrupted", (self.run / "cache-status").read_text())
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=5)

    def test_progress_is_atomic_and_success_is_verified(self):
        progress = self.run / "cache-progress"
        saved = self.run / "cache-status"
        victim = self.temp / "must-not-be-overwritten"
        victim.write_text("protected\n", encoding="utf-8")
        saved.symlink_to(victim)
        env = self.environment()
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
