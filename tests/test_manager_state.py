"""Connection-manager state transitions that do not require a visible display."""

from pathlib import Path
import sys
import types
import unittest
from unittest import mock


REPO_LIBRARY = (Path(__file__).resolve().parents[1] /
                "overlay/usr/local/lib/thinclient")
sys.path.insert(0, str(REPO_LIBRARY if REPO_LIBRARY.is_dir()
                       else "/usr/local/lib/thinclient"))
try:                                      # PyGObject is image-side, not host-side.
    import gi  # noqa: F401
except ImportError:
    manager = None
else:
    # Once GTK is present, any failure in manager itself is a real test error,
    # not a reason to silently skip all of its state-transition coverage.
    import manager


@unittest.skipUnless(manager is not None, "GTK manager dependencies are not installed")
class ManagerState(unittest.TestCase):
    def test_product_title_includes_the_build_version(self):
        self.assertEqual(
            "ThinClient 1.2",
            manager.product_title({"name": "ThinClient", "version": "1.2"}),
        )

    def test_product_title_has_clean_fallbacks(self):
        self.assertEqual(
            "ThinClient",
            manager.product_title({"name": " ThinClient ", "version": "  "}),
        )
        self.assertEqual("ThinClient", manager.product_title({}))

    def test_cpu_info_is_condensed_and_includes_logical_cpu_count(self):
        text = """\
processor   : 0
model name  : Intel(R)   Core(TM) i5-8250U CPU @ 1.60GHz
processor   : 1
model name  : Intel(R) Core(TM) i5-8250U CPU @ 1.60GHz
"""

        self.assertEqual(
            "Intel(R) Core(TM) i5-8250U CPU @ 1.60GHz · 8 logical CPUs",
            manager.parse_cpu_info(text, cpu_count=8),
        )

    def test_cpu_info_falls_back_when_no_model_is_present(self):
        self.assertEqual(
            "Unknown",
            manager.parse_cpu_info("processor : 0\nflags : fpu sse\n", cpu_count=1),
        )

    def test_meminfo_reports_total_memory_in_gibibytes(self):
        text = """\
MemTotal:        8176204 kB
MemFree:          104832 kB
"""

        self.assertEqual("7.8 GiB", manager.parse_meminfo(text))

    def test_meminfo_falls_back_when_total_is_missing(self):
        self.assertEqual("Unknown", manager.parse_meminfo("MemFree: 42 kB\n"))

    def test_lspci_graphics_parses_a_quoted_vga_controller(self):
        text = """\
00:1f.3 \"Audio device\" \"Intel Corporation\" \"Sunrise Point-LP HD Audio\"
00:02.0 \"VGA compatible controller\" \"Intel Corporation\" \"UHD Graphics 620\"
"""

        self.assertEqual(
            "Intel Corporation UHD Graphics 620",
            manager.parse_lspci_graphics(text),
        )

    def test_lspci_graphics_parses_a_quoted_3d_controller(self):
        text = (
            '01:00.0 "3D controller" "NVIDIA Corporation" '
            '"GA107M [GeForce RTX 3050 Ti Mobile]"\n'
        )

        self.assertEqual(
            "NVIDIA Corporation GA107M [GeForce RTX 3050 Ti Mobile]",
            manager.parse_lspci_graphics(text),
        )

    def test_lspci_graphics_falls_back_without_a_display_controller(self):
        text = '00:1f.3 "Audio device" "Intel Corporation" "HD Audio"\n'

        self.assertEqual("Not detected", manager.parse_lspci_graphics(text))

    def test_network_adapter_summary_includes_driver_link_and_speed(self):
        self.assertEqual(
            "enp2s0 · Ethernet · r8169 · connected · 1 Gb/s",
            manager.format_network_adapter("enp2s0", False, "r8169", "up", "1000"),
        )
        self.assertEqual(
            "wlp3s0 · Wi-Fi · iwlwifi · down",
            manager.format_network_adapter("wlp3s0", True, "iwlwifi", "down", "-1"),
        )

    def test_network_addresses_are_static_and_support_friendly(self):
        text = """[
          {"ifname":"lo","addr_info":[{"local":"127.0.0.1","prefixlen":8,"scope":"host"}]},
          {"ifname":"enp2s0","addr_info":[
            {"local":"192.168.10.42","prefixlen":24,"scope":"global"},
            {"local":"fe80::1","prefixlen":64,"scope":"link"}
          ]}
        ]"""

        self.assertEqual(
            {"enp2s0": ["192.168.10.42/24"]},
            manager.parse_ip_addresses(text),
        )
        self.assertEqual(
            "enp2s0 · Ethernet · r8169 · connected · 1 Gb/s · 192.168.10.42/24",
            manager.format_network_adapter(
                "enp2s0", False, "r8169", "up", "1000", ["192.168.10.42/24"]
            ),
        )

    def test_unbound_network_controller_is_visible_for_support(self):
        text = """\
Slot:\t0000:00:1f.6
Class:\tEthernet controller
Vendor:\tIntel Corporation
Device:\tEthernet Connection I219-LM
Module:\te1000e

Slot:\t0000:02:00.0
Class:\tNetwork controller
Vendor:\tIntel Corporation
Device:\tWi-Fi 6 AX200
Driver:\tiwlwifi
Module:\tiwlwifi
"""

        self.assertEqual(
            ["Intel Corporation Ethernet Connection I219-LM · no driver bound"],
            manager.parse_unbound_network_controllers(text),
        )

    def test_hardware_snapshot_is_collected_only_once(self):
        old_cache = manager._HARDWARE_CACHE
        manager._HARDWARE_CACHE = None
        files = {
            "/proc/cpuinfo": "model name: Test CPU\n",
            "/proc/meminfo": "MemTotal: 4194304 kB\n",
        }
        try:
            with mock.patch.object(manager, "_read_local", side_effect=files.get) as read, \
                    mock.patch.object(manager, "run", return_value="") as run, \
                    mock.patch.object(manager, "network_adapter_info",
                                      return_value="eth0 · Ethernet · e1000 · up"), \
                    mock.patch.object(manager.os, "cpu_count", return_value=4), \
                    mock.patch.object(manager.platform, "machine", return_value="x86_64"):
                first = manager.hardware_info()
                second = manager.hardware_info()
        finally:
            manager._HARDWARE_CACHE = old_cache

        self.assertEqual(first, second)
        self.assertIsNot(first, second)
        self.assertEqual(2, read.call_count)
        run.assert_called_once_with(["lspci", "-mm"], timeout=3)

    def test_help_is_public_and_always_destroys_its_dialog(self):
        info = {"name": "ThinClient", "version": "1.2"}
        hardware = {"Architecture": "x86_64"}
        cache = {"state": "network", "summary": "Network boot"}
        window = types.SimpleNamespace(
            info=info, last_error="", set_status=mock.Mock(),
            open_quick_network_test=mock.Mock(),
        )
        dialog = mock.Mock()
        dialog.run.return_value = manager.Gtk.ResponseType.CLOSE

        with mock.patch.object(manager, "hardware_info", return_value=hardware), \
                mock.patch.object(manager.uxstate, "cache_status", return_value=cache), \
                mock.patch.object(manager, "HelpDialog", return_value=dialog) as help_dialog:
            manager.ThinClient.on_help(window)

        help_dialog.assert_called_once_with(window, info, hardware, cache, "")
        dialog.run.assert_called_once_with()
        dialog.destroy.assert_called_once_with()
        window.set_status.assert_not_called()
        window.open_quick_network_test.assert_not_called()
        self.assertEqual(
            "https://github.com/patnawa/Thinclient", manager.GITHUB_URL
        )

    def test_about_compatibility_entry_point_opens_help(self):
        window = types.SimpleNamespace(on_help=mock.Mock(return_value="shown"))

        self.assertEqual("shown", manager.ThinClient.on_about(window))
        window.on_help.assert_called_once_with()

    def test_preflight_stages_dns_and_tcp_without_credentials(self):
        diagnostics = types.SimpleNamespace(
            normalize_target=mock.Mock(return_value={
                "name": "Office", "host": "rdp.example.test", "port": 3389,
            }),
            check_dns=mock.Mock(return_value={
                "ok": True, "addresses": ["192.0.2.10"], "detail": "resolved",
            }),
            check_tcp=mock.Mock(return_value={"ok": True, "detail": "connected"}),
        )
        connection = {
            "name": "Office", "host": "rdp.example.test", "port": 3389,
            "username": "alice", "password": "must-not-leak",
        }
        stage = mock.Mock()

        self.assertEqual(
            (True, ""),
            manager.connection_preflight(connection, diagnostics=diagnostics, stage=stage),
        )
        diagnostics.normalize_target.assert_called_once_with(connection)
        diagnostics.check_dns.assert_called_once_with("rdp.example.test")
        diagnostics.check_tcp.assert_called_once_with("192.0.2.10", 3389)
        self.assertEqual(["Checking network", "Contacting server"],
                         [call.args[0] for call in stage.call_args_list])

    def test_preflight_returns_actionable_dns_and_tcp_failures(self):
        target = {"name": "Office", "host": "rdp.example.test", "port": 3389}
        dns_failure = types.SimpleNamespace(
            normalize_target=mock.Mock(return_value=target),
            check_dns=mock.Mock(return_value={"ok": False, "detail": "not found"}),
            check_tcp=mock.Mock(),
        )
        ok, message = manager.connection_preflight({}, diagnostics=dns_failure)
        self.assertFalse(ok)
        self.assertIn("Check DNS", message)
        dns_failure.check_tcp.assert_not_called()

        tcp_failure = types.SimpleNamespace(
            normalize_target=mock.Mock(return_value=target),
            check_dns=mock.Mock(return_value={
                "ok": True, "addresses": ["192.0.2.10"], "detail": "resolved",
            }),
            check_tcp=mock.Mock(return_value={"ok": False, "detail": "timed out"}),
        )
        ok, message = manager.connection_preflight({}, diagnostics=tcp_failure)
        self.assertFalse(ok)
        self.assertIn("server, firewall, and network route", message)

    def test_reload_received_during_a_session_is_remembered(self):
        window = types.SimpleNamespace(session_active=True, reload_pending=False)

        result = manager.ThinClient.reload_config(window)

        self.assertTrue(result)
        self.assertTrue(window.reload_pending)

    def test_late_auto_connect_looks_up_the_current_connection(self):
        connection = {"id": "kiosk", "name": "Kiosk"}
        callbacks = []
        window = types.SimpleNamespace(
            _auto_connect_id=None,
            _auto_countdown_id=None,
            _countdown_id=None,
            session_active=False,
            cfg={"device": {"auto_connect": "kiosk"},
                 "connections": [connection]},
            _begin_auto_connect=mock.Mock(),
        )

        def schedule(delay, callback):
            self.assertEqual(600, delay)
            callbacks.append(callback)
            return 77

        with mock.patch.object(manager.GLib, "timeout_add", side_effect=schedule):
            manager.ThinClient.schedule_auto_connect(window)

        self.assertEqual(77, window._auto_connect_id)
        self.assertFalse(callbacks[0]())
        window._begin_auto_connect.assert_called_once_with(connection, 5)
        self.assertIsNone(window._auto_connect_id)

    def test_single_left_click_launches_the_connection_card(self):
        connection = {"id": "office", "name": "Office"}
        window = types.SimpleNamespace(
            session_active=False,
            cfg={"connections": [connection]},
            start_session=mock.Mock(),
        )
        row = types.SimpleNamespace(conn_id="office")

        self.assertTrue(manager.ThinClient._on_connection_card_clicked(
            window, row, types.SimpleNamespace(button=1)))
        window.start_session.assert_called_once_with(connection)

        window.start_session.reset_mock()
        self.assertFalse(manager.ThinClient._on_connection_card_clicked(
            window, row, types.SimpleNamespace(button=3)))
        window.start_session.assert_not_called()

    def test_reload_cancels_a_stale_reconnect_countdown(self):
        fresh = {"device": {"auto_connect": ""}, "connections": []}
        dialog = mock.Mock()
        window = types.SimpleNamespace(
            session_active=False,
            reload_pending=False,
            _countdown_id=41,
            _auto_countdown_id=None,
            _countdown_dialog=dialog,
            cfg={"device": {}, "connections": []},
            session_credentials={"old": {"password": "secret"}},
            refresh_list=mock.Mock(),
            set_status=mock.Mock(),
            schedule_auto_connect=mock.Mock(),
            cancel_auto_connect=mock.Mock(),
        )

        with mock.patch.object(manager.GLib, "source_remove") as remove, \
                mock.patch.object(manager.tcconfig, "load", return_value=fresh):
            manager.ThinClient.reload_config(window)

        remove.assert_called_once_with(41)
        self.assertIsNone(window._countdown_id)
        dialog.response.assert_called_once_with(manager.Gtk.ResponseType.CANCEL)
        self.assertEqual({}, window.session_credentials)
        self.assertIs(fresh, window.cfg)
        window.schedule_auto_connect.assert_called_once_with()
        window.cancel_auto_connect.assert_called_once_with(silent=True)

    def test_session_completion_applies_a_pending_reload(self):
        window = types.SimpleNamespace(
            session_active=True,
            session_proc=mock.Mock(),
            connect_btn=mock.Mock(),
            reload_pending=True,
            reload_config=mock.Mock(return_value=True),
            _close_progress=mock.Mock(),
            session_cancelled=False,
            last_error="",
            show_all=mock.Mock(),
            present=mock.Mock(),
            set_status=mock.Mock(),
            show_connection_error=mock.Mock(),
        )

        result = manager.ThinClient._session_done(
            window, {"name": "Old session"}, 0, None
        )

        self.assertIsNone(result)
        self.assertFalse(window.reload_pending)
        window.reload_config.assert_called_once_with()
        window.set_status.assert_called_with("Session to Old session ended.")

    def test_nonretryable_failure_forgets_a_transient_password(self):
        window = types.SimpleNamespace(
            session_active=True,
            session_proc=mock.Mock(),
            connect_btn=mock.Mock(),
            reload_pending=False,
            _close_progress=mock.Mock(),
            session_cancelled=False,
            last_error="",
            session_credentials={"main": {"password": "wrong"}},
            cancel_reconnect=False,
            _countdown_id=None,
            show_all=mock.Mock(),
            present=mock.Mock(),
            set_status=mock.Mock(),
            show_connection_error=mock.Mock(),
        )
        connection = {"id": "main", "name": "Main", "auto_reconnect": True}

        with mock.patch.object(
                manager.tcconfig, "explain_failure",
                return_value=manager.tcconfig.Failure("Wrong password", False)):
            manager.ThinClient._session_done(window, connection, 131, None)

        self.assertNotIn("main", window.session_credentials)

    def test_cancel_race_after_process_launch_terminates_client(self):
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.return_value = -15
        window = types.SimpleNamespace(
            cfg={"device": {"session_bar": True}},
            session_cancelled=False,
            session_proc=None,
            _session_launched=mock.Mock(),
        )

        def launch(*_args, **_kwargs):
            window.session_cancelled = True
            return process

        with mock.patch.object(
                manager.tcconfig, "build_command",
                return_value=(["xfreerdp3", "/v:server"], None)), \
                mock.patch.object(manager.tcconfig, "prepare_environment",
                                  return_value={}), \
                mock.patch.object(manager.subprocess, "Popen", side_effect=launch), \
                mock.patch.object(manager.os, "makedirs"), \
                mock.patch.object(manager.os.path, "exists", return_value=False), \
                mock.patch("builtins.open", mock.mock_open()), \
                mock.patch.object(manager.GLib, "idle_add") as idle_add:
            code, error = manager.ThinClient._run_session(
                window, {"name": "Office"}, "secret"
            )

        self.assertEqual(-15, code)
        self.assertIsNone(error)
        process.terminate.assert_called_once_with()
        idle_add.assert_not_called()
        self.assertIsNone(window.session_proc)

    def test_admin_settings_failure_is_visible(self):
        window = types.SimpleNamespace(
            _open_settings_authorised=mock.Mock(
                side_effect=RuntimeError("dialog unavailable")),
            set_status=mock.Mock(),
        )

        result = manager.ThinClient._open_settings(window, authorised=True)

        self.assertIsNone(result)
        window.set_status.assert_called_once_with(
            "Settings failed: dialog unavailable", bad=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
