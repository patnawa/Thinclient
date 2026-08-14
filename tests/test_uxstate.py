"""Display-independent tests for the task-first UI presentation state."""

from pathlib import Path
import sys
import tempfile
import unittest


LIBRARY = Path(__file__).resolve().parents[1] / "overlay/usr/local/lib/thinclient"
sys.path.insert(0, str(LIBRARY))
import uxstate


class ConnectionPresentation(unittest.TestCase):
    def test_friendly_defaults_hide_raw_endpoints(self):
        connection = {"host": "secret.internal", "protocol": "rdp", "app": ""}
        self.assertEqual("Connections", uxstate.connection_group(connection))
        self.assertEqual("Remote desktop", uxstate.connection_description(connection))
        self.assertEqual("RDP", uxstate.connection_badge(connection))
        self.assertNotIn("secret.internal", uxstate.connection_description(connection))

    def test_remoteapp_vnc_and_configured_labels(self):
        self.assertEqual("RemoteApp", uxstate.connection_badge(
            {"protocol": "rdp", "app": "||Accounting"}))
        self.assertEqual("Remote support session", uxstate.connection_description(
            {"protocol": "vnc"}))
        self.assertEqual("Finance", uxstate.connection_group({"group": " Finance "}))
        self.assertEqual("Month-end accounting", uxstate.connection_description(
            {"description": " Month-end   accounting "}))


class ChangelogPresentation(unittest.TestCase):
    def test_reads_bounded_offline_changelog(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "CHANGELOG.md"
            path.write_text("## 1.4\n\n- New UI\n" + "x" * 100, encoding="utf-8")
            text = uxstate.changelog_text(path, limit=32)
        self.assertTrue(text.startswith("## 1.4"))
        self.assertIn("Older entries omitted", text)

    def test_missing_changelog_has_readable_fallback(self):
        self.assertIn("not available", uxstate.changelog_text("/missing/changelog"))

    def test_repository_changelog_contains_current_release(self):
        changelog = (Path(__file__).resolve().parents[1] / "CHANGELOG.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("## 1.4", changelog)


class CachePresentation(unittest.TestCase):
    def test_hit_and_network_states_are_clear(self):
        hit = uxstate.cache_status(
            "state=hit\nprofile=lite\ndevice=/dev/sdb1\n", "", "")
        self.assertEqual("hit", hit["state"])
        self.assertIn("verified", hit["summary"])
        self.assertIn("Lite", hit["summary"])

        network = uxstate.cache_status("state=network\nprofile=full\n", "", "")
        self.assertEqual("network", network["state"])
        self.assertEqual("Network boot · Full", network["summary"])

    def test_progress_wins_and_is_bounded(self):
        status = uxstate.cache_status(
            "state=network\nprofile=lite\n", "",
            "state=saving\nprofile=lite\npercent=140\n")
        self.assertEqual("saving", status["state"])
        self.assertEqual(100, status["percent"])
        self.assertIn("100%", status["summary"])

    def test_legacy_saved_status_remains_compatible(self):
        status = uxstate.cache_status(
            "state=network\nprofile=lite\n",
            "saved lite abc /dev/sdb1\n", "")
        self.assertEqual("saved", status["state"])
        self.assertEqual("/dev/sdb1", status["detail"])


class NetworkPresentation(unittest.TestCase):
    REPORT = """Network diagnostics
Target: Office — 192.0.2.10:3389
Local: enp2s0 — 192.168.10.42/24
Default gateway: 192.168.10.1
Gateway ping (informational): FAILED — no reply
DNS: OK — not needed (IP address)
Route to target: OK — enp2s0 from 192.168.10.42 via 192.168.10.1
TCP 3389: OK — connected
RDP: OK — HYBRID — TLS with CredSSP/NLA
No credentials were sent.
"""

    def test_report_becomes_visual_rows_without_treating_ping_as_fatal(self):
        rows = uxstate.parse_network_report(self.REPORT)
        self.assertEqual(
            ["Network", "Gateway", "DNS", "Route", "Server port 3389",
             "RDP handshake"],
            [row["label"] for row in rows],
        )
        self.assertEqual("ok", rows[1]["state"])
        self.assertEqual("failed", rows[1]["ping_state"])
        self.assertTrue(all(row["state"] == "ok" for row in rows))

    def test_missing_local_network_is_a_failure(self):
        rows = uxstate.parse_network_report(
            "Local: no active interface — no IPv4 address\n"
            "DNS: FAILED — name not found\n")
        self.assertEqual("failed", rows[0]["state"])
        self.assertEqual("failed", rows[2]["state"])


class SupportReport(unittest.TestCase):
    def test_report_contains_support_state_but_no_connection_secret(self):
        report = uxstate.support_report(
            {"version": "1.3", "base": "Debian trixie", "kernel": "6.12"},
            {"Architecture": "x86_64", "Network": "e1000e · connected"},
            "TC-01", "192.168.10.42",
            {"summary": "USB boot cache verified · Lite"},
            "Server did not respond",
        )
        self.assertIn("USB boot cache verified", report)
        self.assertIn("Server did not respond", report)
        self.assertIn("Image profile: Unknown", report)
        self.assertIn("No credentials are included", report)


if __name__ == "__main__":
    unittest.main()
