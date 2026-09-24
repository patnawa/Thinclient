"""Early boot skips absent USB storage but still waits for late block nodes."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "overlay/usr/lib/live/boot/9991-thinclient-cache.sh"


@unittest.skipUnless(shutil.which("sh"), "requires POSIX shell")
class CacheDiscovery(unittest.TestCase):
    def probe(self, usb=False, busy=False, override=False, appears=None):
        code = SOURCE.read_text() + "\n" + '''
TC_CACHE_LABEL=TCCACHE
slept=0
sleep() { slept=$((slept + $1)); }
'''
        code += "LIVE_BOOT_CMDLINE='%s'\n" % ("tc.cache.wait=1" if override else "")
        code += "udevadm() { return %d; }\n" % int(busy)
        code += "tc_cache_usb_present() { return %d; }\n" % int(not usb)
        # These cases model storage that is still attaching, not a settled
        # reader with an empty slot; CacheDiscoverySysfs covers the latter.
        code += "tc_cache_usb_pending() { return %d; }\n" % int(not (usb or busy))
        if appears is None:
            code += "blkid() { return 1; }\n"
        else:
            code += "blkid() { [ \"$tries\" -ge %d ] && printf '/dev/test-usb'; }\n" % appears
        code += 'tc_cache_devices\nresult=$?\nprintf "\\nresult=%s slept=%s\\n" "$result" "$slept"\n'
        return subprocess.run(["sh"], input=code, text=True, capture_output=True, check=True).stdout

    def test_no_usb_storage_has_no_retry_sleeps(self):
        self.assertIn("result=1 slept=0", self.probe())

    def test_late_block_device_is_still_discovered(self):
        output = self.probe(usb=True, appears=2)
        self.assertIn("/dev/test-usb", output)
        self.assertIn("result=0 slept=2", output)

    def test_present_storage_keeps_bounded_retry(self):
        self.assertIn("result=1 slept=5", self.probe(usb=True))

    def test_unsettled_usb_queue_keeps_discovery_window(self):
        self.assertIn("result=1 slept=5", self.probe(busy=True))

    def test_explicit_controller_compatibility_override(self):
        self.assertIn("result=1 slept=5", self.probe(override=True))


@unittest.skipUnless(shutil.which("sh"), "requires POSIX shell")
class CacheDiscoverySysfs(unittest.TestCase):
    """Drive the real USB storage probes against a fake sysfs tree.

    Field logs showed some desktops (internal card readers, a stick left in)
    paying the full five-second retry window on every boot even though their
    USB storage had long finished enumerating without a TCCACHE label.
    """

    def setUp(self):
        self.sysfs = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.sysfs)

    def interface(self, name, block=True, driver=True):
        path = self.sysfs / "bus/usb/devices" / name
        path.mkdir(parents=True)
        (path / "bInterfaceClass").write_text("08\n")
        if driver:
            (path / "driver").mkdir()
        if block:
            (path / "host6/target6:0:0/6:0:0:0/block/sdb").mkdir(parents=True)
        return path

    def probe(self, appears_after=None, attach_after=None):
        code = SOURCE.read_text() + "\n" + '''
TC_CACHE_LABEL=TCCACHE
LIVE_BOOT_CMDLINE=''
slept=0
udevadm() { return 0; }
'''
        code += "TC_SYSFS='%s'\n" % self.sysfs.as_posix()
        if attach_after is None:
            code += "sleep() { slept=$((slept + $1)); }\n"
        else:
            # A slow reader gains its block node only after some retries.
            code += (
                "sleep() { slept=$((slept + $1)); [ \"$slept\" -lt %d ] || "
                "mkdir -p \"$TC_SYSFS/bus/usb/devices/1-1:1.0/host6/target6:0:0/6:0:0:0/block/sdb\"; }\n"
                % attach_after
            )
        if appears_after is None:
            code += "blkid() { return 1; }\n"
        else:
            code += "blkid() { [ \"$slept\" -ge %d ] && printf '/dev/test-usb'; }\n" % appears_after
        code += 'tc_cache_devices\nresult=$?\nprintf "\\nresult=%s slept=%s\\n" "$result" "$slept"\n'
        return subprocess.run(["sh"], input=code, text=True, capture_output=True, check=True).stdout

    def test_enumerated_storage_without_cache_label_does_not_wait(self):
        self.interface("1-1:1.0")
        self.assertIn("result=1 slept=0", self.probe())

    def test_multi_slot_reader_without_cache_label_does_not_wait(self):
        self.interface("1-1:1.0")
        self.interface("1-2:1.0")
        self.assertIn("result=1 slept=0", self.probe())

    def test_storage_still_attaching_keeps_retrying(self):
        self.interface("1-1:1.0", block=False)
        output = self.probe(attach_after=2, appears_after=2)
        self.assertIn("/dev/test-usb", output)
        self.assertIn("result=0 slept=2", output)

    def test_storage_that_never_attaches_stays_bounded(self):
        self.interface("1-1:1.0", block=False)
        self.assertIn("result=1 slept=5", self.probe())

    def test_unbound_storage_interface_stays_bounded(self):
        self.interface("1-1:1.0", block=False, driver=False)
        self.assertIn("result=1 slept=5", self.probe())
