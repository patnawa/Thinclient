"""Behaviour of the small HTTP server used for PXE and central configuration."""

import contextlib
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
            {"root": str(self.root), "log_message": lambda *_args: None},
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
