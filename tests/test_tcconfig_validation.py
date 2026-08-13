"""Validation of configuration received from removable or remote sources."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

repo_module_dir = Path(__file__).resolve().parents[1] / "overlay/usr/local/lib/thinclient"
sys.path.insert(0, str(repo_module_dir if repo_module_dir.is_dir()
                       else "/usr/local/lib/thinclient"))
import tcconfig  # noqa: E402


class ConfigValidation(unittest.TestCase):
    def load_payload(self, payload):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(payload, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return tcconfig.load(layers=(handle.name,))

    def test_numbers_are_clamped_and_malformed_values_use_defaults(self):
        cfg = self.load_payload({
            "device": {"screen_blank_minutes": 999},
            "connections": [
                {"id": "low", "port": -10, "reconnect_delay": 0},
                {"id": "high", "port": 70000, "reconnect_delay": 999},
                {"id": "bad", "port": "not-a-port", "reconnect_delay": 2.5},
                {"id": "strings", "port": "3390", "reconnect_delay": "30"},
            ],
        })

        self.assertEqual(180, cfg["device"]["screen_blank_minutes"])
        by_id = {conn["id"]: conn for conn in cfg["connections"]}
        self.assertEqual((1, 2), (by_id["low"]["port"], by_id["low"]["reconnect_delay"]))
        self.assertEqual(
            (65535, 120),
            (by_id["high"]["port"], by_id["high"]["reconnect_delay"]),
        )
        self.assertEqual(
            (tcconfig.CONNECTION_DEFAULTS["port"],
             tcconfig.CONNECTION_DEFAULTS["reconnect_delay"]),
            (by_id["bad"]["port"], by_id["bad"]["reconnect_delay"]),
        )
        self.assertEqual(
            (3390, 30),
            (by_id["strings"]["port"], by_id["strings"]["reconnect_delay"]),
        )

        for supplied, expected in ((-1, 0), ("45", 45), ("bad", 0), (True, 0)):
            with self.subTest(screen_blank_minutes=supplied):
                device = self.load_payload({
                    "device": {"screen_blank_minutes": supplied}
                })["device"]
                self.assertEqual(expected, device["screen_blank_minutes"])

    def test_booleans_only_accept_explicit_values(self):
        cfg = self.load_payload({
            "device": {
                "allow_settings": "false",
                "allow_console": "YES",
                "allow_terminal": 0,
                "session_bar": 1,
                "show_ip": "perhaps",
            },
            "connections": [{
                "prompt_credentials": "off",
                "audio_out": "ON",
                "audio_in": 0,
                "redirect_clipboard": 1,
                "redirect_usb_devices": 2,
                "auto_reconnect": [],
            }],
        })

        device = cfg["device"]
        self.assertFalse(device["allow_settings"])
        self.assertTrue(device["allow_console"])
        self.assertFalse(device["allow_terminal"])
        self.assertTrue(device["session_bar"])
        self.assertTrue(device["show_ip"], "ambiguous values must use the default")

        conn = cfg["connections"][0]
        self.assertFalse(conn["prompt_credentials"])
        self.assertTrue(conn["audio_out"])
        self.assertFalse(conn["audio_in"])
        self.assertTrue(conn["redirect_clipboard"])
        self.assertFalse(conn["redirect_usb_devices"])
        self.assertTrue(conn["auto_reconnect"])

    def test_strings_and_enums_are_safe_and_canonical(self):
        cfg = self.load_payload({
            "device": {
                "hostname_prefix": ["unsafe"],
                "keyboard_layout": 7,
                "admin_password": {"hash": "wrong type"},
            },
            "connections": [
                {
                    "id": {"not": "hashable"},
                    "name": ["not text"],
                    "host": "  server.example.com  ",
                    "username": False,
                    "password": "  significant whitespace  ",
                    "protocol": "VNC",
                    "cert_policy": "STRICT",
                    "security": "TLS",
                    "gfx": "AVC444",
                    "network": "LAN",
                    "display": "1920X1080",
                },
                {
                    "id": "invalid-enums",
                    "protocol": "ssh",
                    "cert_policy": 3,
                    "security": "magic",
                    "gfx": "ultra",
                    "network": "satellite",
                    "display": "huge",
                },
            ],
        })

        self.assertEqual("thin", cfg["device"]["hostname_prefix"])
        self.assertEqual("us", cfg["device"]["keyboard_layout"])
        self.assertEqual("", cfg["device"]["admin_password"])

        canonical, invalid = cfg["connections"]
        self.assertIsInstance(canonical["id"], str)
        self.assertTrue(canonical["id"])
        self.assertEqual("server.example.com", canonical["host"])
        self.assertEqual("server.example.com", canonical["name"])
        self.assertEqual("", canonical["username"])
        self.assertEqual("  significant whitespace  ", canonical["password"])
        self.assertEqual(
            ("vnc", "strict", "tls", "avc444", "lan", "1920x1080"),
            tuple(canonical[key] for key in (
                "protocol", "cert_policy", "security", "gfx", "network", "display"
            )),
        )
        for key in tcconfig.CONNECTION_ENUMS:
            expected = "strict" if key == "cert_policy" else tcconfig.CONNECTION_DEFAULTS[key]
            self.assertEqual(expected, invalid[key])
        self.assertEqual(tcconfig.CONNECTION_DEFAULTS["display"], invalid["display"])

        with mock.patch.object(tcconfig, "freerdp_binary", return_value="xfreerdp3"):
            argv, _stdin = tcconfig.build_command(invalid, cfg["device"])
        self.assertNotIn("/cert:ignore", argv)

    def test_ids_are_nonempty_unique_and_do_not_shadow_explicit_ids(self):
        cfg = self.load_payload({
            "device": {"auto_connect": " dup "},
            "connections": [
                {"id": ""},
                {"id": 42},
                {"id": "dup"},
                {"id": "dup"},
                {"id": "conn1"},
            ],
        })

        ids = [conn["id"] for conn in cfg["connections"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(isinstance(conn_id, str) and conn_id for conn_id in ids))
        self.assertEqual(1, ids.count("dup"))
        self.assertEqual(1, ids.count("conn1"))
        self.assertEqual("dup", cfg["device"]["auto_connect"])

    def test_auto_connect_is_cleared_when_its_connection_does_not_exist(self):
        cfg = self.load_payload({
            "device": {"auto_connect": "removed"},
            "connections": [{"id": "current"}],
        })

        self.assertEqual("", cfg["device"]["auto_connect"])

    def test_non_object_layers_are_ignored_instead_of_crashing(self):
        for payload in ([], ["unexpected"], "unexpected", 7, True, None):
            with self.subTest(payload=payload):
                cfg = self.load_payload(payload)
                self.assertEqual([], cfg["connections"])
                self.assertEqual(tcconfig.DEVICE_DEFAULTS, cfg["device"])

    def test_bad_higher_layer_values_cannot_remove_lockdown(self):
        lower = {
            "device": {
                "admin_password": "sha256$salt$digest",
                "allow_settings": False,
                "allow_terminal": False,
                "allow_console": False,
            }
        }
        higher = {
            "device": {
                "admin_password": {"wrong": "type"},
                "allow_settings": [],
                "allow_terminal": "typo",
                "allow_console": 2,
            }
        }
        paths = []
        for payload in (lower, higher):
            handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
            json.dump(payload, handle)
            handle.close()
            self.addCleanup(os.unlink, handle.name)
            paths.append(handle.name)

        device = tcconfig.load(layers=paths)["device"]

        self.assertEqual("sha256$salt$digest", device["admin_password"])
        self.assertFalse(device["allow_settings"])
        self.assertFalse(device["allow_terminal"])
        self.assertFalse(device["allow_console"])

    def test_extra_arguments_are_cleaned_and_round_trip_through_the_editor(self):
        args = ["/printer:HP,HP Color LaserJet 2800 Series PS", "-themes"]
        cfg = self.load_payload({
            "connections": [{"extra_args": ["  " + args[0] + "  ", "", 3, "-themes"]}]
        })

        self.assertEqual(args, cfg["connections"][0]["extra_args"])
        self.assertEqual(args, tcconfig.parse_extra_args(tcconfig.format_extra_args(args)))
        self.assertEqual("", tcconfig.format_extra_args("not-an-argv-list"))

    def test_nmcli_terse_fields_preserve_escaped_colons_and_backslashes(self):
        self.assertEqual(
            ["Cafe:5G", "82", "WPA2"],
            tcconfig.parse_nmcli_terse(r"Cafe\:5G:82:WPA2"),
        )
        self.assertEqual(
            ["office\\guest", "70", "WPA3"],
            tcconfig.parse_nmcli_terse(r"office\\guest:70:WPA3"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
