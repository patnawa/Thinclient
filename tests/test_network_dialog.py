"""Headless state-transition tests for the Network dialog's test tab.

The production methods are exercised on small widget doubles, so these tests
need PyGObject but never connect to a display or touch the host network.
"""

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
    settings = None
else:
    # Importing GTK does not require a display.  No widget is constructed here.
    import settings


def fake_dialog(connection=None):
    """Return a display-free receiver for NetworkDialog's state methods."""
    buffer_ = mock.Mock(name="report_buffer")
    report = mock.Mock(name="report_view")
    report.get_buffer.return_value = buffer_
    dialog = types.SimpleNamespace(
        _test_generation=0,
        _test_destroyed=False,
        _selected_test_target=mock.Mock(return_value=connection or {}),
        test_run=mock.Mock(name="run_button"),
        test_copy=mock.Mock(name="copy_button"),
        test_spinner=mock.Mock(name="spinner"),
        test_report=report,
        message=mock.Mock(name="message"),
        get_display=mock.Mock(return_value=object()),
    )
    dialog._finish_network_test = types.MethodType(
        settings.NetworkDialog._finish_network_test, dialog
    )
    return dialog, buffer_


@unittest.skipUnless(settings is not None, "GTK settings dependencies are not installed")
class NetworkDialogLifecycle(unittest.TestCase):
    def test_worker_is_deferred_and_receives_only_a_secret_free_snapshot(self):
        connection = {
            "id": "office",
            "name": "Office RDP",
            "host": "rdp.example.test",
            "port": 3389,
            "protocol": "rdp",
            "gateway": "gateway.example.test",
            "username": "alice",
            "password": "do-not-copy",
            "domain": "CORP",
            "extra_args": ["/password:also-secret"],
        }
        dialog, buffer_ = fake_dialog(connection)
        thread = mock.Mock(name="network_thread")
        thread_factory = mock.Mock(return_value=thread)
        idle_add = mock.Mock(name="idle_add")

        with mock.patch.object(
                settings, "threading", types.SimpleNamespace(Thread=thread_factory)), \
                mock.patch.object(
                    settings, "GLib", types.SimpleNamespace(idle_add=idle_add)), \
                mock.patch.object(settings.networkdiag, "run_preflight",
                                  return_value="finished") as preflight:
            settings.NetworkDialog._run_network_test(dialog)

            # Starting the OS thread must not synchronously perform the probe.
            thread_factory.assert_called_once()
            self.assertTrue(thread_factory.call_args.kwargs["daemon"])
            self.assertEqual("network-preflight",
                             thread_factory.call_args.kwargs["name"])
            thread.start.assert_called_once_with()
            preflight.assert_not_called()
            idle_add.assert_not_called()

            # Mutating the connection after the click cannot alter the worker's
            # immutable, allow-listed snapshot.
            connection["host"] = "changed.example.test"
            connection["password"] = "changed-secret"
            thread_factory.call_args.kwargs["target"]()

        preflight.assert_called_once_with({
            "name": "Office RDP",
            "host": "rdp.example.test",
            "port": 3389,
            "protocol": "rdp",
            "gateway": "gateway.example.test",
        })
        callback, generation, report = idle_add.call_args.args
        self.assertEqual(dialog._finish_network_test, callback)
        self.assertEqual(1, generation)
        self.assertEqual("finished", report)
        dialog.test_run.set_sensitive.assert_called_once_with(False)
        dialog.test_copy.set_sensitive.assert_called_once_with(False)
        dialog.test_spinner.start.assert_called_once_with()
        self.assertIn("rdp.example.test:3389",
                      buffer_.set_text.call_args.args[0])

    def test_stale_and_destroyed_callbacks_do_not_touch_widgets(self):
        dialog, buffer_ = fake_dialog()
        dialog._test_generation = 4

        self.assertFalse(settings.NetworkDialog._finish_network_test(
            dialog, 3, "stale report"
        ))
        buffer_.set_text.assert_not_called()
        dialog.test_spinner.stop.assert_not_called()
        dialog.test_run.set_sensitive.assert_not_called()
        dialog.test_copy.set_sensitive.assert_not_called()

        settings.NetworkDialog._on_test_destroyed(dialog)
        self.assertTrue(dialog._test_destroyed)
        self.assertEqual(5, dialog._test_generation)
        self.assertFalse(settings.NetworkDialog._finish_network_test(
            dialog, 5, "late report"
        ))
        buffer_.set_text.assert_not_called()
        dialog.test_spinner.stop.assert_not_called()

    def test_worker_exception_is_visible_and_reenables_controls(self):
        dialog, buffer_ = fake_dialog({
            "name": "Broken server", "host": "broken.example.test", "port": 3389,
        })
        deferred = {}

        def make_thread(**kwargs):
            deferred["worker"] = kwargs["target"]
            return types.SimpleNamespace(start=mock.Mock())

        def idle_add(callback, *args):
            deferred["callback"] = (callback, args)
            return 17

        with mock.patch.object(
                settings, "threading", types.SimpleNamespace(Thread=make_thread)), \
                mock.patch.object(
                    settings, "GLib", types.SimpleNamespace(idle_add=idle_add)), \
                mock.patch.object(settings.networkdiag, "run_preflight",
                                  side_effect=RuntimeError("probe exploded")):
            settings.NetworkDialog._run_network_test(dialog)
            deferred["worker"]()

        callback, args = deferred["callback"]
        self.assertFalse(callback(*args))
        self.assertEqual(
            "Network diagnostics\nFAILED — probe exploded",
            buffer_.set_text.call_args.args[0],
        )
        dialog.test_spinner.stop.assert_called_once_with()
        self.assertEqual(
            [mock.call(False), mock.call(True)],
            dialog.test_run.set_sensitive.call_args_list,
        )
        self.assertEqual(
            [mock.call(False), mock.call(True)],
            dialog.test_copy.set_sensitive.call_args_list,
        )

    def test_copy_report_places_the_full_text_on_the_clipboard(self):
        dialog, buffer_ = fake_dialog()
        buffer_.get_start_iter.return_value = "start"
        buffer_.get_end_iter.return_value = "end"
        buffer_.get_text.return_value = "Network diagnostics\nTCP: OK"
        clipboard = mock.Mock(name="clipboard")
        clipboard_type = types.SimpleNamespace(
            get_default=mock.Mock(return_value=clipboard)
        )

        with mock.patch.object(
                settings, "Gtk", types.SimpleNamespace(Clipboard=clipboard_type)):
            settings.NetworkDialog._copy_network_report(dialog)

        clipboard_type.get_default.assert_called_once_with(
            dialog.get_display.return_value
        )
        buffer_.get_text.assert_called_once_with("start", "end", True)
        clipboard.set_text.assert_called_once_with(
            "Network diagnostics\nTCP: OK", -1
        )
        dialog.message.set_text.assert_called_once_with(
            "Network test report copied to the clipboard."
        )


if __name__ == "__main__":
    unittest.main()
