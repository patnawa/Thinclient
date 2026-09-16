"""Configuration outages and rejected policy must not replace a valid copy."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

LIBRARY = Path(__file__).resolve().parents[1] / "overlay/usr/local/lib/thinclient"
sys.path.insert(0, str(LIBRARY if LIBRARY.is_dir() else Path("/usr/local/lib/thinclient")))
import configfetch
import tcconfig


class ConfigFetch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name)
        (self.run / ".root").mkdir(mode=0o700)
        self.payload = {"schema": 1, "version": "test-2", "connections": [{"host": "desktop.example"}]}

    def download(self, url, path, mac, deadline):
        path.write_text(json.dumps(self.payload))

    def test_success_has_version_and_no_url_or_credentials_in_status(self):
        status = configfetch.fetch("https://example/config.json?token=secret", self.run,
                                   downloader=self.download)
        self.assertEqual("current", status["state"])
        self.assertEqual("test-2", status["version"])
        self.assertTrue(status["authenticated"])
        self.assertNotIn("secret", json.dumps(status))
        self.assertNotIn("desktop.example", json.dumps(status))

    def test_timeout_preserves_last_known_good(self):
        previous = json.dumps(self.payload)
        (self.run / "remote-config.json").write_text(previous)
        def timeout(*args):
            raise subprocess.TimeoutExpired("curl", 20)
        status = configfetch.fetch("https://example/config.json", self.run,
                                   downloader=timeout)
        self.assertEqual("stale", status["state"])
        self.assertEqual(previous, (self.run / "remote-config.json").read_text())

    def test_invalid_schema_does_not_publish(self):
        self.payload["schema"] = 999
        status = configfetch.fetch("https://example/config.json", self.run,
                                   downloader=self.download)
        self.assertEqual("unavailable", status["state"])
        self.assertFalse((self.run / "remote-config.json").exists())

    def test_required_https_rejects_http_before_downloading(self):
        def unexpected(*args):
            self.fail("must reject HTTP before contacting a server")
        status = configfetch.fetch("http://example/config.json", self.run,
                                   {"require_https": True, "config_required": True},
                                   downloader=unexpected)
        self.assertEqual("unavailable", status["state"])
        self.assertTrue(status["required"])

    def test_missing_required_configuration_is_unavailable(self):
        status = configfetch.fetch("", self.run, {"config_required": True})
        self.assertEqual("unavailable", status["state"])

    def test_invalid_signature_cannot_publish(self):
        def reject(*args):
            raise ValueError("bad signature")
        status = configfetch.fetch("http://example/config.json", self.run,
                                   {"config_public_key": "/trusted/key.pem"},
                                   downloader=self.download, verifier=reject)
        self.assertEqual("unavailable", status["state"])
        self.assertFalse((self.run / "remote-config.json").exists())

    def test_status_symlink_does_not_overwrite_target(self):
        if os.name != "posix":
            self.skipTest("POSIX symlinks")
        victim = self.run / "protected"
        victim.write_text("untouched")
        (self.run / "config-status.json").symlink_to(victim)
        configfetch.fetch("", self.run)
        self.assertEqual("untouched", victim.read_text())
        self.assertFalse((self.run / "config-status.json").is_symlink())

    def test_production_validation_rejects_insecure_defaults(self):
        errors = configfetch.validate({"device": {}, "connections": [
            {"cert_policy": "ignore", "password": "do-not-store"}]}, production=True)
        self.assertEqual(3, len(errors))
        self.assertNotIn("do-not-store", json.dumps(errors))

    def test_malformed_immutable_policy_blocks_connections(self):
        def read(path):
            if str(path).endswith("policy.json"):
                return None
            if str(path).endswith("config-status.json"):
                return {"state": "current"}
            return {"connections": [{"id": "main", "host": "desktop.example"}]}
        with mock.patch.object(tcconfig, "_read", side_effect=read), \
                mock.patch.object(tcconfig.os.path, "exists", return_value=True):
            configuration = tcconfig.load()
        self.assertTrue(configuration["configuration_blocked"])
        self.assertEqual([], configuration["connections"])
