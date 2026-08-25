"""Validation at the privileged device-configuration boundary."""

import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


REPO_SCRIPT = (Path(__file__).resolve().parents[1] /
               "overlay/usr/local/sbin/tc-apply-config")
SCRIPT = REPO_SCRIPT if REPO_SCRIPT.is_file() else Path("/usr/local/sbin/tc-apply-config")
REPO_LIBRARY = (Path(__file__).resolve().parents[1] /
                "overlay/usr/local/lib/thinclient")
if REPO_LIBRARY.is_dir():
    sys.path.insert(0, str(REPO_LIBRARY))
LOADER = importlib.machinery.SourceFileLoader("tc_apply_config", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
tc_apply_config = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(tc_apply_config)


class RootConfigValidation(unittest.TestCase):
    def test_xkb_identifiers_allow_normal_values_and_reject_file_injection(self):
        self.assertEqual("us,th", tc_apply_config.safe_xkb_value(" us,th ", "us"))
        self.assertEqual(
            ",nodeadkeys",
            tc_apply_config.safe_xkb_value(",nodeadkeys", "", variant=True),
        )
        for malicious in ('us"\nEVDEV="evil', "us\nOTHER=value", "$(touch /tmp/x)"):
            with self.subTest(value=malicious):
                self.assertEqual(
                    "us", tc_apply_config.safe_xkb_value(malicious, "us")
                )

    def test_ntp_list_rejects_newlines_and_shell_metacharacters(self):
        self.assertEqual(
            "dc01.example.com 192.0.2.10",
            tc_apply_config.safe_ntp_servers("dc01.example.com 192.0.2.10"),
        )
        for malicious in ("pool.ntp.org\nFallbackNTP=evil", "$(command)", "server;reboot"):
            with self.subTest(value=malicious):
                self.assertEqual("", tc_apply_config.safe_ntp_servers(malicious))

    def test_ntp_list_accepts_comma_separated_input(self):
        self.assertEqual(
            "dc01.example.com dc02.example.com",
            tc_apply_config.safe_ntp_servers("dc01.example.com, dc02.example.com"),
        )

    def test_timesyncd_dropin_resets_the_list_before_assigning(self):
        # The image ships a default NTP= in 10-thinclient.conf; list settings
        # combine across drop-ins, so the runtime file must reset the list or
        # the configured server merely queues behind the default.
        self.assertEqual(
            "[Time]\nNTP=\nNTP=dc01.example.com\n",
            tc_apply_config.timesyncd_dropin("dc01.example.com"),
        )

    def test_timezone_must_resolve_to_a_file_inside_zoneinfo(self):
        with tempfile.TemporaryDirectory() as private:
            root = Path(private, "zoneinfo")
            root.mkdir()
            zone = root / "Asia" / "Bangkok"
            zone.parent.mkdir()
            zone.write_bytes(b"zone")
            outside = Path(private, "outside-zone")
            outside.write_bytes(b"outside")

            self.assertEqual(
                str(zone.resolve()),
                tc_apply_config.timezone_path("Asia/Bangkok", str(root)),
            )
            self.assertIsNone(
                tc_apply_config.timezone_path("../outside-zone", str(root))
            )
            self.assertIsNone(tc_apply_config.timezone_path("Asia", str(root)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
