"""Early boot skips absent USB storage but still waits for late block nodes."""
from pathlib import Path
import shutil
import subprocess
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
