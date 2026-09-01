"""Behaviour of the small HTTP server used for PXE and central configuration."""

import contextlib
import concurrent.futures
import datetime
import http.client
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock


SERVER_PATH = Path(__file__).resolve().parents[1] / "tools/tc-config-server.py"
SERVER_AVAILABLE = SERVER_PATH.is_file()
if SERVER_AVAILABLE:
    spec = importlib.util.spec_from_file_location("tc_config_server", SERVER_PATH)
    config_server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config_server)
else:                           # The server is host-side, not installed in the image.
    config_server = None


@unittest.skipUnless(SERVER_AVAILABLE, "tc-config-server.py is not installed in the client image")
class ConfigurationSummary(unittest.TestCase):
    def test_valid_connections_are_summarised(self):
        summary, error = config_server.configuration_summary({
            "connections": [{"host": "rdp.example.com"}, {"host": ""}]
        })

        self.assertEqual("rdp.example.com, ?", summary)
        self.assertIsNone(error)

    def test_wrong_shaped_documents_return_a_warning_instead_of_raising(self):
        malformed_documents = (
            [],
            "not an object",
            {"connections": {}},
            {"connections": ["not an object"]},
            {"connections": [{"host": 3389}]},
        )

        for document in malformed_documents:
            with self.subTest(document=document):
                summary, error = config_server.configuration_summary(document)
                self.assertIsNone(summary)
                self.assertTrue(error)

    def test_startup_warns_for_wrong_shape_and_still_starts_server(self):
        class NonServingServer:
            started = False

            def __init__(self, *_args):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def serve_forever(self):
                type(self).started = True

        with tempfile.TemporaryDirectory() as root:
            Path(root, "config.json").write_text("[]", encoding="utf-8")
            output = io.StringIO()
            argv = ["tc-config-server.py", "--root", root, "--port", "0"]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(config_server, "Server", NonServingServer), \
                    mock.patch.object(config_server, "local_addresses", return_value=[]), \
                    contextlib.redirect_stdout(output):
                config_server.main()

        self.assertIn("not a valid ThinClient config", output.getvalue())
        self.assertTrue(NonServingServer.started)


@unittest.skipUnless(SERVER_AVAILABLE, "tc-config-server.py is not installed in the client image")
class StatusTimestampFormatting(unittest.TestCase):
    def test_utc_timestamp_is_rendered_in_bangkok_time(self):
        bangkok = datetime.timezone(datetime.timedelta(hours=7))

        rendered = config_server.format_status_timestamp(
            "2026-09-01T09:58:49Z", bangkok
        )

        self.assertEqual("2026-09-01 16:58:49+07:00", rendered)

    def test_json_timestamp_formatter_remains_utc(self):
        self.assertEqual(
            "2026-09-01T09:58:49Z",
            config_server.utc_timestamp(1788256729),
        )

    def test_sort_options_are_allowlisted_and_bounded(self):
        self.assertEqual(
            ("clients.ip", "asc"),
            config_server.status_sort_from_url(
                "/status?sort=clients.ip&dir=sideways"
            ),
        )
        self.assertEqual(
            (None, None),
            config_server.status_sort_from_url(
                "/status?sort=%3Cscript%3E&dir=asc"
            ),
        )
        too_many = "/status?" + "&".join("field%d=x" % index for index in range(9))
        self.assertEqual(
            (None, None), config_server.status_sort_from_url(too_many)
        )

    def test_ip_sort_is_natural_and_does_not_mutate_snapshot_rows(self):
        rows = [
            {"ip": "192.168.10.10"},
            {"ip": "not-an-ip"},
            {"ip": None},
            {"ip": "192.168.10.2"},
        ]

        sorted_rows = config_server.sort_status_rows(
            rows, "clients", "clients.ip", "asc"
        )

        self.assertEqual(
            ["192.168.10.2", "192.168.10.10", "not-an-ip", None],
            [row["ip"] for row in sorted_rows],
        )
        self.assertEqual("192.168.10.10", rows[0]["ip"])

    def test_server_rendered_sort_marks_heading_and_orders_rows(self):
        monitor = config_server.StatusMonitor()
        for address in ("192.168.10.10", "192.168.10.2"):
            request_id = monitor.begin("GET", "/config.json", address)
            monitor.finish(request_id, 200)
        snapshot = monitor.snapshot()
        snapshot["health"] = {"status": "ok", "missing": [], "problems": []}

        page = config_server.status_html(
            snapshot,
            datetime.timezone(datetime.timedelta(hours=7)),
            "Asia/Bangkok",
            "clients.ip",
            "asc",
        )

        self.assertLess(page.index("192.168.10.2"), page.index("192.168.10.10"))
        self.assertIn('aria-sort="ascending"', page)
        self.assertIn("sort=clients.ip&amp;dir=desc", page)
        self.assertNotIn("<script", page)

    def test_dashboard_escapes_monitor_values(self):
        monitor = config_server.StatusMonitor()
        request_id = monitor.begin(
            "GET", '/<img src=x onerror="alert(1)">', "192.0.2.1"
        )
        monitor.finish(request_id, 404)
        snapshot = monitor.snapshot()
        snapshot["health"] = {"status": "ok", "missing": [], "problems": []}

        page = config_server.status_html(snapshot)

        self.assertNotIn('<img src=x onerror="alert(1)">', page)
        self.assertIn("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;", page)


@unittest.skipUnless(SERVER_AVAILABLE, "tc-config-server.py is not installed in the client image")
class ConfigServerHttp(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.base = Path(self.tempdir.name)
        self.root = self.base / "pxe"
        self.root.mkdir()

        handler = type(
            "QuietHandler",
            (config_server.Handler,),
            {
                "root": str(self.root),
                "status_monitor": config_server.StatusMonitor(),
                "status_timezone": datetime.timezone(
                    datetime.timedelta(hours=7)
                ),
                "status_timezone_name": "Asia/Bangkok",
                "log_message": lambda *_args: None,
            },
        )
        self.server = config_server.Server(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method, path, headers=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=3
        )
        self.addCleanup(connection.close)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    def write(self, relative_path, content):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_directory_listing_is_disabled(self):
        self.write("config-aa-bb-cc-dd-ee-ff.json", "device config")

        status, _headers, body = self.request("GET", "/")

        self.assertEqual(404, status)
        self.assertNotIn(b"config-aa-bb-cc-dd-ee-ff.json", body)

    def test_direct_per_device_config_requests_are_rejected(self):
        self.write("config-aa-bb-cc-dd-ee-ff.json", "device config")

        for method in ("GET", "HEAD"):
            for path in (
                    "/config-aa-bb-cc-dd-ee-ff.json",
                    "/config-aa-bb-cc-dd-ee-ff.json.",
                    "/config-aa-bb-cc-dd-ee-ff.json%20",
                    "/config-aa-bb-cc-dd-ee-ff.json::$DATA",
                    "/CONFIG-AA-BB-CC-DD-EE-FF.JSON",
            ):
                with self.subTest(method=method, path=path):
                    status, _headers, body = self.request(
                        method,
                        path,
                        {"X-ThinClient-MAC": "aa:bb:cc:dd:ee:ff"},
                    )
                    self.assertEqual(404, status)
                    if method == "HEAD":
                        self.assertEqual(b"", body)

    def test_valid_mac_selects_override_for_get_and_head(self):
        global_body = json.dumps({"source": "global"})
        device_body = json.dumps({"source": "device", "padding": "longer"})
        self.write("config.json", global_body)
        self.write("config-aa-bb-cc-dd-ee-ff.json", device_body)
        mac_header = {"X-ThinClient-MAC": "aa:bb:cc:dd:ee:ff"}

        get_status, get_headers, body = self.request("GET", "/config.json", mac_header)
        head_status, head_headers, head_body = self.request(
            "HEAD", "/config.json", mac_header
        )

        self.assertEqual(200, get_status)
        self.assertEqual(device_body.encode(), body)
        self.assertEqual(200, head_status)
        self.assertEqual(b"", head_body)
        self.assertEqual(str(len(body)), get_headers["Content-Length"])
        self.assertEqual(get_headers["Content-Length"], head_headers["Content-Length"])

    def test_invalid_mac_gets_global_config(self):
        global_body = json.dumps({"source": "global"})
        self.write("config.json", global_body)
        self.write("config-aa-bb-cc-dd-ee-ff.json", '{"source": "device"}')

        status, _headers, body = self.request(
            "GET", "/config.json", {"X-ThinClient-MAC": "not-a-mac"}
        )

        self.assertEqual(200, status)
        self.assertEqual(global_body.encode(), body)

    def test_percent_encoded_filename_is_served(self):
        self.write("hello world.txt", "hello")

        status, _headers, body = self.request("GET", "/hello%20world.txt")

        self.assertEqual(200, status)
        self.assertEqual(b"hello", body)

    def test_encoded_traversal_cannot_leave_root(self):
        (self.base / "outside.txt").write_text("secret", encoding="utf-8")

        status, _headers, body = self.request("GET", "/%2e%2e/outside.txt")

        self.assertEqual(404, status)
        self.assertNotIn(b"secret", body)

    def test_invalid_utf8_and_encoded_nul_are_rejected(self):
        for path in ("/%ff", "/config.json%00.txt"):
            with self.subTest(path=path):
                status, _headers, _body = self.request("GET", path)
                self.assertEqual(404, status)

    def test_health_and_status_requests_are_not_counted(self):
        status, headers, body = self.request("GET", "/healthz")

        self.assertEqual(503, status)
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertEqual("degraded", json.loads(body)["status"])

        self.write("config.json", "{}")
        status, _headers, body = self.request("GET", "/healthz")
        self.assertEqual(200, status)
        self.assertEqual("ok", json.loads(body)["status"])

        status, _headers, body = self.request("GET", "/status.json")
        snapshot = json.loads(body)
        self.assertEqual(200, status)
        self.assertEqual("ok", snapshot["status"])
        self.assertEqual(0, snapshot["totals"]["requests"])
        self.assertEqual([], snapshot["recent_clients"])

    def test_status_json_tracks_identified_clients_and_served_bytes(self):
        config_body = '{"connections": []}'
        self.write("config.json", config_body)

        status, _headers, body = self.request(
            "GET", "/config.json",
            {"X-ThinClient-MAC": "AA:BB:CC:DD:EE:FF"},
        )
        self.assertEqual(200, status)
        self.assertEqual(config_body.encode(), body)

        status, _headers, body = self.request("GET", "/status.json")
        snapshot = json.loads(body)
        client = snapshot["recent_clients"][0]
        request = snapshot["recent_requests"][0]

        self.assertEqual(200, status)
        self.assertEqual(1, snapshot["totals"]["requests"])
        self.assertEqual(1, snapshot["totals"]["successful_requests"])
        self.assertEqual(len(config_body), snapshot["totals"]["bytes_sent"])
        self.assertEqual("aa:bb:cc:dd:ee:ff", client["mac"])
        self.assertEqual("/config.json", client["last_path"])
        self.assertEqual(200, client["last_status"])
        self.assertEqual(len(config_body), client["bytes_sent"])
        self.assertEqual(200, request["status"])

    def test_status_html_is_available_for_get_and_head(self):
        self.write("config.json", "{}")

        status, headers, body = self.request("GET", "/status")
        head_status, head_headers, head_body = self.request("HEAD", "/status/")

        self.assertEqual(200, status)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertIn(b"ThinClient PXE HTTP", body)
        self.assertIn(b"/status.json", body)
        self.assertIn(b"timezone Asia/Bangkok", body)
        self.assertIn(b"Updated ", body)
        self.assertIn(b"+07:00 (Asia/Bangkok)", body)
        self.assertEqual(200, head_status)
        self.assertEqual(b"", head_body)
        self.assertGreater(int(head_headers["Content-Length"]), 0)

    def test_status_sort_query_is_safe_and_survives_refresh(self):
        self.write("config.json", "{}")

        status, headers, body = self.request(
            "GET", "/status?sort=requests.status&dir=asc"
        )
        invalid_status, _invalid_headers, invalid_body = self.request(
            "GET", "/status?sort=%3Cscript%3E&dir=sideways"
        )

        self.assertEqual(200, status)
        self.assertEqual(200, invalid_status)
        self.assertIn(b'aria-sort="ascending"', body)
        self.assertIn(b"sort=requests.status&amp;dir=desc", body)
        self.assertIn(b"default-src 'none'", headers["Content-Security-Policy"].encode())
        self.assertNotIn(b"<script", invalid_body)

    def test_failed_static_requests_appear_in_status(self):
        status, _headers, _body = self.request("GET", "/missing.img")
        self.assertEqual(404, status)

        _status, _headers, body = self.request("GET", "/status.json")
        snapshot = json.loads(body)

        self.assertEqual(1, snapshot["totals"]["failed_requests"])
        self.assertEqual(404, snapshot["recent_requests"][0]["status"])
        self.assertEqual("/missing.img", snapshot["recent_requests"][0]["path"])


@unittest.skipUnless(SERVER_AVAILABLE, "tc-config-server.py is not installed in the client image")
class StatusMonitorState(unittest.TestCase):
    def test_anonymous_boot_activity_merges_when_mac_arrives(self):
        monitor = config_server.StatusMonitor()
        root_request = monitor.begin(
            "GET", "/thinclient/lite/filesystem.squashfs", "192.0.2.10"
        )
        monitor.set_content_length(root_request, 1024)
        monitor.add_bytes(root_request, 1024)
        monitor.finish(root_request, 200)

        config_request = monitor.begin(
            "GET", "/config.json", "192.0.2.10", "aa:bb:cc:dd:ee:ff"
        )
        monitor.finish(config_request, 200)
        snapshot = monitor.snapshot()

        self.assertEqual(1, snapshot["totals"]["clients"])
        self.assertEqual(1, snapshot["totals"]["boots"])
        self.assertEqual(2, snapshot["totals"]["requests"])
        client = snapshot["recent_clients"][0]
        self.assertEqual("aa:bb:cc:dd:ee:ff", client["mac"])
        self.assertEqual("lite", client["profile"])
        self.assertEqual(1, client["boots"])
        self.assertEqual(2, client["requests"])
        self.assertEqual(1024, client["bytes_sent"])

    def test_snapshot_reports_active_transfer_progress(self):
        monitor = config_server.StatusMonitor()
        request_id = monitor.begin("GET", "/large.img", "192.0.2.20")
        monitor.set_content_length(request_id, 1000)
        monitor.add_bytes(request_id, 250)

        snapshot = monitor.snapshot()
        transfer = snapshot["active_transfers"][0]

        self.assertEqual(1, snapshot["totals"]["active_transfers"])
        self.assertEqual(25.0, transfer["progress_percent"])
        self.assertEqual(250, transfer["bytes_sent"])

    def test_interrupted_transfer_is_failed_and_not_counted_as_a_boot(self):
        monitor = config_server.StatusMonitor()
        request_id = monitor.begin(
            "GET", "/thinclient/lite/filesystem.squashfs", "192.0.2.30"
        )
        monitor.add_bytes(request_id, 512)
        monitor.finish(request_id, 200, interrupted=True)
        snapshot = monitor.snapshot()
        snapshot["health"] = {"status": "ok", "missing": [], "problems": []}

        self.assertEqual(1, snapshot["totals"]["failed_requests"])
        self.assertEqual(0, snapshot["totals"]["boots"])
        self.assertTrue(snapshot["recent_requests"][0]["interrupted"])
        self.assertIn("200 interrupted", config_server.status_html(snapshot))

    def test_completed_history_survives_monitor_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory, "http-status.json")
            first = config_server.StatusMonitor(state_file=str(state_file))
            root_request = first.begin(
                "GET", "/thinclient/lite/filesystem.squashfs", "192.0.2.40"
            )
            first.add_bytes(root_request, 2048)
            first.finish(root_request, 200)
            config_request = first.begin(
                "GET", "/config.json", "192.0.2.40", "00:11:22:33:44:55"
            )
            first.add_bytes(config_request, 128)
            first.finish(config_request, 200)
            original = first.snapshot()

            restarted = config_server.StatusMonitor(state_file=str(state_file))
            restored = restarted.snapshot()

        self.assertEqual(original["history_started_at"], restored["history_started_at"])
        self.assertEqual(2, restored["totals"]["requests"])
        self.assertEqual(2, restored["totals"]["successful_requests"])
        self.assertEqual(1, restored["totals"]["boots"])
        self.assertEqual(2176, restored["totals"]["bytes_sent"])
        self.assertEqual(2, restored["totals"]["server_starts"])
        self.assertEqual("00:11:22:33:44:55", restored["recent_clients"][0]["mac"])
        self.assertEqual("ok", restored["persistence"]["status"])

    def test_inflight_request_is_recovered_as_interrupted_after_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory, "http-status.json")
            first = config_server.StatusMonitor(state_file=str(state_file))
            first.begin(
                "GET", "/thinclient/full/filesystem.squashfs", "192.0.2.50"
            )

            restarted = config_server.StatusMonitor(state_file=str(state_file))
            restored = restarted.snapshot()

        self.assertEqual(0, restored["totals"]["active_transfers"])
        self.assertEqual(1, restored["totals"]["failed_requests"])
        self.assertEqual(1, restored["totals"]["recovered_interrupted_requests"])
        self.assertEqual(0, restored["totals"]["boots"])
        recovered = restored["recent_requests"][0]
        self.assertTrue(recovered["interrupted"])
        self.assertTrue(recovered["recovered_after_restart"])
        self.assertEqual(0, recovered["status"])

    def test_persistence_failure_degrades_health_without_stopping_monitor(self):
        with tempfile.TemporaryDirectory() as directory:
            blocker = Path(directory, "not-a-directory")
            blocker.write_text("file", encoding="utf-8")
            monitor = config_server.StatusMonitor(
                state_file=str(blocker / "http-status.json")
            )
            persistence = monitor.persistence_report()
            health = config_server.health_report(directory, persistence)

        self.assertEqual("error", persistence["status"])
        self.assertEqual("degraded", health["status"])
        self.assertTrue(health["problems"])

    def test_invalid_state_is_preserved_and_replaced_with_clean_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory, "http-status.json")
            state_file.write_text("not json", encoding="utf-8")
            warnings = io.StringIO()
            with contextlib.redirect_stderr(warnings):
                monitor = config_server.StatusMonitor(state_file=str(state_file))

            backups = list(Path(directory).glob("http-status.json.corrupt-*"))
            state = json.loads(state_file.read_text(encoding="utf-8"))

        self.assertEqual(1, len(backups))
        self.assertIn("invalid HTTP status state", warnings.getvalue())
        self.assertEqual(config_server.StatusMonitor.STATE_VERSION, state["version"])
        self.assertEqual("ok", monitor.persistence_report()["status"])

    def test_concurrent_updates_leave_restartable_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory, "http-status.json")
            monitor = config_server.StatusMonitor(state_file=str(state_file))

            def complete_request(index):
                request_id = monitor.begin(
                    "GET", "/thinclient/lite/vmlinuz", "192.0.2.%d" % (index + 1)
                )
                monitor.add_bytes(request_id, 4096)
                monitor.finish(request_id, 200)

            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
                list(executor.map(complete_request, range(64)))

            restarted = config_server.StatusMonitor(state_file=str(state_file))
            restored = restarted.snapshot()

        self.assertEqual(64, restored["totals"]["requests"])
        self.assertEqual(64, restored["totals"]["successful_requests"])
        self.assertEqual(64 * 4096, restored["totals"]["bytes_sent"])
        self.assertEqual(64, restored["totals"]["clients"])
        self.assertEqual(0, restored["totals"]["recovered_interrupted_requests"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
