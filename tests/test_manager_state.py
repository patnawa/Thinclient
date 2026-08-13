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
            _countdown_id=None,
            session_active=False,
            cfg={"device": {"auto_connect": "kiosk"},
                 "connections": [connection]},
            start_session=mock.Mock(),
        )

        def schedule(delay, callback):
            self.assertEqual(600, delay)
            callbacks.append(callback)
            return 77

        with mock.patch.object(manager.GLib, "timeout_add", side_effect=schedule):
            manager.ThinClient.schedule_auto_connect(window)

        self.assertEqual(77, window._auto_connect_id)
        self.assertFalse(callbacks[0]())
        window.start_session.assert_called_once_with(connection)
        self.assertIsNone(window._auto_connect_id)

    def test_reload_cancels_a_stale_reconnect_countdown(self):
        fresh = {"device": {"auto_connect": ""}, "connections": []}
        dialog = mock.Mock()
        window = types.SimpleNamespace(
            session_active=False,
            reload_pending=False,
            _countdown_id=41,
            _countdown_dialog=dialog,
            cfg={"device": {}, "connections": []},
            session_credentials={"old": {"password": "secret"}},
            refresh_list=mock.Mock(),
            set_status=mock.Mock(),
            schedule_auto_connect=mock.Mock(),
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

    def test_session_completion_applies_a_pending_reload(self):
        window = types.SimpleNamespace(
            session_active=True,
            connect_btn=mock.Mock(),
            reload_pending=True,
            reload_config=mock.Mock(return_value=True),
            show_all=mock.Mock(),
            present=mock.Mock(),
            set_status=mock.Mock(),
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
            connect_btn=mock.Mock(),
            reload_pending=False,
            session_credentials={"main": {"password": "wrong"}},
            cancel_reconnect=False,
            _countdown_id=None,
            show_all=mock.Mock(),
            present=mock.Mock(),
            set_status=mock.Mock(),
        )
        connection = {"id": "main", "name": "Main", "auto_reconnect": True}

        with mock.patch.object(
                manager.tcconfig, "explain_failure",
                return_value=manager.tcconfig.Failure("Wrong password", False)):
            manager.ThinClient._session_done(window, connection, 131, None)

        self.assertNotIn("main", window.session_credentials)


if __name__ == "__main__":
    unittest.main(verbosity=2)
