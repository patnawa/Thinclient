"""Load safety, checksum enforcement and honest VM timing boundaries."""
import hashlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


benchmark = load("pxe_benchmark", "pxe-benchmark.py")
timer = load("boot_timer", "boot-timer.py")


class LoadSafety(unittest.TestCase):
    def test_remote_load_requires_explicit_opt_in(self):
        with self.assertRaisesRegex(ValueError, "allow-remote"):
            benchmark.validate_url("http://192.0.2.1/root.squashfs")
        self.assertEqual("http://192.0.2.1/root.squashfs",
                         benchmark.validate_url("http://192.0.2.1/root.squashfs", True))

    def test_loopback_is_allowed_without_external_load(self):
        for host in ("127.0.0.1", "localhost", "[::1]"):
            benchmark.validate_url("http://" + host + "/root.squashfs")

    def test_credentials_queries_and_invalid_schemes_are_refused(self):
        for url in ("file:///tmp/root", "http://user:secret@localhost/root",
                    "http://localhost/root?token=secret", "http://localhost/root#fragment"):
            with self.assertRaises(ValueError):
                benchmark.validate_url(url)

    def test_concurrency_is_bounded(self):
        self.assertEqual([1, 10, 50], benchmark.parse_clients("1,10,50"))
        for value in ("0", "129", "-1", "bad"):
            with self.assertRaises(ValueError):
                benchmark.parse_clients(value)

    def transfer(self, received, expected, code=0):
        process = mock.MagicMock()
        process.__enter__.return_value = process
        process.stdout = io.BytesIO(received)
        process.stderr = io.BytesIO()
        process.wait.return_value = code
        with mock.patch.object(benchmark.subprocess, "Popen", return_value=process) as command:
            result = benchmark.download("http://127.0.0.1/root", hashlib.sha256(expected).hexdigest(), len(expected), 10)
        self.assertEqual(["curl", "--disable"], command.call_args.args[0][:2])
        self.assertIn("--max-time", command.call_args.args[0])
        self.assertNotIn("--location", command.call_args.args[0])
        return result

    def test_corrupt_and_truncated_success_responses_are_not_successful(self):
        self.assertFalse(self.transfer(b"bad", b"yes")["verified"])
        self.assertFalse(self.transfer(b"ye", b"yes")["verified"])
        self.assertFalse(self.transfer(b"yes", b"yes", code=28)["verified"])
        self.assertTrue(self.transfer(b"yes", b"yes")["verified"])


class BootTiming(unittest.TestCase):
    def test_host_time_includes_more_than_kernel_uptime_and_drops_secrets(self):
        report = timer.measurement({"uptime_seconds": 12, "version": "test", "password": "secret"}, 100, 128.5)
        self.assertEqual(28.5, report["host_elapsed_seconds"])
        self.assertEqual(12, report["kernel_to_ui_seconds"])
        self.assertEqual("vm-launch-to-ui", report["scope"])
        self.assertNotIn("password", report)
