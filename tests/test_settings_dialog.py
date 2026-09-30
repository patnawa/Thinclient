"""Exercise connection editing with real GTK signals on a virtual display."""

import copy
from pathlib import Path
import sys
import unittest
from unittest import mock

LIBRARY = Path(__file__).resolve().parents[1] / "overlay/usr/local/lib/thinclient"
sys.path.insert(0, str(LIBRARY if LIBRARY.is_dir() else Path("/usr/local/lib/thinclient")))
try:
    import gi
except ImportError:
    settings = None
else:
    import settings
    import tcconfig


@unittest.skipUnless(settings is not None, "requires GTK")
class SettingsEditing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not settings.Gtk.init_check()[0]:
            raise unittest.SkipTest("requires a display; run with xvfb-run")

    def dialog(self, *connections):
        cfg = tcconfig.load(layers=[])
        cfg["connections"] = [
            dict(copy.deepcopy(tcconfig.CONNECTION_DEFAULTS),
                 **{"id": "conn%d" % index, "name": "Office %d" % index,
                    "host": "desktop.example.test", **connection})
            for index, connection in enumerate(connections)
        ]
        with mock.patch.object(settings.BackgroundJob, "start", return_value=False):
            dialog = settings.SettingsDialog(None, cfg)
        self.addCleanup(dialog.destroy)
        return dialog

    def test_opening_connection_preserves_an_explicit_nonstandard_port(self):
        for protocol, port in (("rdp", 5900), ("vnc", 3389)):
            with self.subTest(protocol=protocol):
                dialog = self.dialog({"protocol": protocol, "port": port})
                self.assertEqual(port, dialog.f["port"].get_value_as_int())
                dialog.response(settings.Gtk.ResponseType.OK)
                self.assertEqual(port, dialog.result["connections"][0]["port"])

    def test_explicit_protocol_switch_still_updates_a_standard_port(self):
        dialog = self.dialog({"protocol": "rdp", "port": 3389})
        dialog.f["protocol"].set_active_id("vnc")
        self.assertEqual(5900, dialog.f["port"].get_value_as_int())
        dialog.f["protocol"].set_active_id("rdp")
        self.assertEqual(3389, dialog.f["port"].get_value_as_int())

    def test_switching_rows_cannot_hide_invalid_connection_from_save(self):
        dialog = self.dialog({}, {})
        dialog.f["host"].set_text("")
        dialog._select_index(1)
        self.assertFalse(dialog.save_button.get_sensitive())
        accepted = []
        dialog.connect("response", lambda _dialog, response: accepted.append(response))
        dialog.response(settings.Gtk.ResponseType.OK)
        self.assertEqual([], accepted)
        self.assertEqual("conn0", dialog.current)

    def test_removing_last_invalid_connection_reenables_save(self):
        dialog = self.dialog({})
        dialog.f["host"].set_text("")
        self.assertFalse(dialog.save_button.get_sensitive())
        dialog._on_remove()
        self.assertEqual([], dialog.result["connections"])
        self.assertTrue(dialog.save_button.get_sensitive())
        self.assertFalse(dialog.form.get_sensitive())

    def test_new_and_renamed_connections_update_startup_choices(self):
        dialog = self.dialog({})
        dialog._on_add()
        new_id = dialog.current
        dialog.f["name"].set_text("New workspace")
        dialog.f["host"].set_text("new.example.test")
        choices = {row[1]: row[0] for row in dialog.d["auto_connect"].get_model()}
        self.assertEqual("Connect to New workspace", choices.get(new_id))
        dialog.d["auto_connect"].set_active_id(new_id)
        dialog.response(settings.Gtk.ResponseType.OK)
        self.assertEqual(new_id, dialog.result["device"]["auto_connect"])

    def test_removing_startup_connection_resets_the_choice(self):
        dialog = self.dialog({}, {})
        dialog.d["auto_connect"].set_active_id("conn0")
        dialog._on_remove()
        choices = {row[1] for row in dialog.d["auto_connect"].get_model()}
        self.assertNotIn("conn0", choices)
        self.assertEqual("", dialog.d["auto_connect"].get_active_id())


if __name__ == "__main__":
    unittest.main()
