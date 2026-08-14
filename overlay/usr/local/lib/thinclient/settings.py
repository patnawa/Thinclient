#!/usr/bin/env python3
"""Settings and network dialogs for the ThinClient connection manager.

Imported lazily by manager.py so that none of this costs anything on the path
between power-on and the connection list.
"""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

import copy  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402

sys.path.insert(0, "/usr/local/lib/thinclient")
import tcconfig  # noqa: E402
import networkdiag  # noqa: E402
import uxstate  # noqa: E402

TIMEZONES = [
    "Asia/Bangkok", "Asia/Singapore", "Asia/Tokyo", "Asia/Shanghai", "Asia/Kolkata",
    "Asia/Dubai", "Europe/London", "Europe/Berlin", "Europe/Paris", "America/New_York",
    "America/Chicago", "America/Los_Angeles", "Australia/Sydney", "UTC",
]
KEYMAPS = ["us", "gb", "th", "de", "fr", "es", "it", "jp", "kr", "cn", "ru", "br", "latam"]

DISPLAY_MODES = [("fullscreen", "Full screen"), ("multimon", "All monitors"),
                 ("window", "Window"), ("custom", "Custom size...")]
CERT_MODES = [("ignore", "Accept any certificate (LAN)"),
              ("tofu", "Trust on first use"),
              ("strict", "Verify against installed CAs")]
SEC_MODES = [("auto", "Negotiate"), ("nla", "NLA (CredSSP)"), ("tls", "TLS"), ("rdp", "Legacy RDP")]
GFX_MODES = [("auto", "Automatic"), ("avc444", "H.264 AVC444"), ("avc420", "H.264 AVC420"),
             ("rfx", "RemoteFX"), ("none", "Plain bitmap")]
NET_MODES = [("auto", "Detect"), ("lan", "LAN"), ("broadband", "Broadband"), ("modem", "Slow link")]


# ----------------------------------------------------------------- widgets ---
def combo(pairs, active_key):
    widget = Gtk.ComboBoxText()
    for index, (key, label) in enumerate(pairs):
        widget.append(key, label)
        if key == active_key:
            widget.set_active(index)
    if widget.get_active() < 0:
        widget.set_active(0)
    return widget


def combo_entry(options, value):
    widget = Gtk.ComboBoxText.new_with_entry()
    for option in options:
        widget.append_text(option)
    widget.get_child().set_text(value or "")
    return widget


class FormGrid(Gtk.Grid):
    """A two-column label/widget grid that keeps track of its own row cursor."""

    def __init__(self):
        super().__init__(row_spacing=7, column_spacing=12, margin=16)
        self._row = 0

    def add_row(self, text, widget):
        label = Gtk.Label(label=text, xalign=1)
        label.set_valign(Gtk.Align.CENTER)
        self.attach(label, 0, self._row, 1, 1)
        widget.set_hexpand(True)
        self.attach(widget, 1, self._row, 1, 1)
        self._row += 1
        return widget

    def add_wide(self, widget):
        self.attach(widget, 0, self._row, 2, 1)
        self._row += 1
        return widget

    def add_heading(self, text):
        label = Gtk.Label(xalign=0, margin_top=10)
        label.set_markup("<b>%s</b>" % GLib.markup_escape_text(text))
        return self.add_wide(label)


# ---------------------------------------------------------------- settings ---
class SettingsDialog(Gtk.Dialog):
    def __init__(self, parent, cfg):
        super().__init__(title="Settings", transient_for=parent, modal=True)
        self.set_default_size(940, 660)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.save_button = self.add_button("Save", Gtk.ResponseType.OK)
        self.save_button.get_style_context().add_class("suggested-action")

        self.result = copy.deepcopy(cfg)
        self.current = None                     # id of the connection being edited

        notebook = Gtk.Notebook()
        notebook.append_page(self._connections_page(), Gtk.Label(label="Connections"))
        notebook.append_page(self._device_page(), Gtk.Label(label="Device"))
        notebook.append_page(self._diagnostics_page(), Gtk.Label(label="Diagnostics"))
        self.get_content_area().pack_start(notebook, True, True, 0)

        self.connect("response", self._on_response)
        self.show_all()
        self._select_index(0)

    # -------------------------------------------------------- connections ---
    def _connections_page(self):
        page = Gtk.Box(spacing=10, margin=10)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        left.set_size_request(240, -1)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.conn_list = Gtk.ListBox()
        self.conn_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.conn_list.connect("row-selected", self._on_row_selected)
        scroller.add(self.conn_list)
        left.pack_start(scroller, True, True, 0)

        buttons = Gtk.Box(spacing=4, homogeneous=True)
        for text, handler in (("Add", self._on_add), ("Copy", self._on_copy),
                              ("Remove", self._on_remove)):
            btn = Gtk.Button(label=text)
            btn.connect("clicked", handler)
            buttons.pack_start(btn, True, True, 0)
        left.pack_start(buttons, False, False, 0)
        page.pack_start(left, False, False, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.form = Gtk.Stack()
        self.form.set_transition_type(Gtk.StackTransitionType.NONE)
        self.basic_form = FormGrid()
        self.advanced_form = FormGrid()
        for name, title, form in (("basic", "Basic", self.basic_form),
                                  ("advanced", "Advanced", self.advanced_form)):
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroller.add(form)
            self.form.add_titled(scroller, name, title)
        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self.form)
        switcher.set_halign(Gtk.Align.START)
        right.pack_start(switcher, False, False, 0)
        right.pack_start(self.form, True, True, 0)
        page.pack_start(right, True, True, 0)

        self._build_form()
        self._reload_conn_list()
        return page

    def _build_form(self):
        basic = self.basic_form
        advanced = self.advanced_form
        self.f = {}
        basic.add_heading("Workspace")
        self.f["name"] = basic.add_row("Display name", Gtk.Entry())
        self.f["description"] = basic.add_row("Description", Gtk.Entry())
        self.f["description"].set_placeholder_text("e.g. Primary workspace")
        self.f["group"] = basic.add_row("Group", Gtk.Entry())
        self.f["group"].set_placeholder_text("e.g. Desktops or Applications")
        self.f["protocol"] = basic.add_row(
            "Protocol", combo([("rdp", "RDP - Windows Remote Desktop"),
                               ("vnc", "VNC - TigerVNC viewer")], "rdp"))
        self.f["protocol"].connect("changed", self._on_protocol_changed)
        self.f["host"] = basic.add_row("Server address", Gtk.Entry())
        self.f["host"].set_placeholder_text("hostname or IP of the Windows server")
        self.f["port"] = basic.add_row("Port", Gtk.SpinButton.new_with_range(1, 65535, 1))
        basic.add_heading("Sign in")
        self.f["username"] = basic.add_row("Username", Gtk.Entry())
        self.f["domain"] = basic.add_row("Domain", Gtk.Entry())
        self.f["password"] = basic.add_row("Password", Gtk.Entry(visibility=False))
        self.f["password"].set_placeholder_text("leave empty to ask the user")
        self.f["prompt_credentials"] = basic.add_wide(
            Gtk.CheckButton(label="Ask for credentials at connection time"))

        basic.add_heading("Display")
        self.f["display"] = basic.add_row("Mode", combo(DISPLAY_MODES, "fullscreen"))
        self.f["display_custom"] = basic.add_row("Custom size", Gtk.Entry())
        self.f["display_custom"].set_placeholder_text("1920x1080")
        self.f["display"].connect("changed", self._on_display_changed)
        self.validation = Gtk.Label(xalign=0)
        self.validation.set_line_wrap(True)
        basic.add_wide(self.validation)

        advanced.add_heading("Performance")
        self.f["gfx"] = advanced.add_row("Graphics codec", combo(GFX_MODES, "auto"))
        self.f["network"] = advanced.add_row("Link speed", combo(NET_MODES, "auto"))

        advanced.add_heading("Security")
        self.f["security"] = advanced.add_row("Protocol", combo(SEC_MODES, "auto"))
        self.f["cert_policy"] = advanced.add_row("Certificates", combo(CERT_MODES, "ignore"))
        self.f["gateway"] = advanced.add_row("RD Gateway", Gtk.Entry())
        self.f["gateway"].set_placeholder_text("optional: gateway.example.com")
        self.f["gateway_username"] = advanced.add_row("Gateway user", Gtk.Entry())
        self.f["gateway_domain"] = advanced.add_row("Gateway domain", Gtk.Entry())

        advanced.add_heading("Redirection")
        for key, text in (("audio_out", "Play remote sound on this client"),
                          ("audio_in", "Redirect the microphone"),
                          ("redirect_clipboard", "Share the clipboard"),
                          ("redirect_usb_storage", "Redirect USB storage as a drive"),
                          ("redirect_usb_devices", "Redirect raw USB devices"),
                          ("redirect_smartcard", "Redirect smart card readers"),
                          ("redirect_printers", "Redirect local printers")):
            self.f[key] = advanced.add_wide(Gtk.CheckButton(label=text))

        advanced.add_heading("Session")
        self.f["app"] = advanced.add_row("RemoteApp", Gtk.Entry())
        self.f["app"].set_placeholder_text("optional: ||AppAlias or a program path")
        self.f["auto_reconnect"] = advanced.add_wide(
            Gtk.CheckButton(label="Reconnect automatically if the session drops"))
        self.f["reconnect_delay"] = advanced.add_row(
            "Reconnect delay (s)", Gtk.SpinButton.new_with_range(2, 120, 1))
        self.f["extra_args"] = advanced.add_row("Extra FreeRDP arguments", Gtk.Entry())
        self.f["extra_args"].set_placeholder_text("/scale:140 -themes")

        for key in ("name", "host", "gateway"):
            self.f[key].connect("changed", self._validate_form)
        for key in ("port", "protocol"):
            self.f[key].connect("changed", self._validate_form)

    def _on_display_changed(self, widget):
        self.f["display_custom"].set_sensitive(widget.get_active_id() == "custom")

    def _validate_form(self, *_):
        if not self.current:
            self.validation.set_text("")
            self.save_button.set_sensitive(True)
            return True
        name = self.f["name"].get_text().strip()
        if not name:
            message = "Display name is required."
        else:
            candidate = {
                "name": name,
                "host": self.f["host"].get_text().strip(),
                "port": int(self.f["port"].get_value()),
                "protocol": self.f["protocol"].get_active_id() or "rdp",
                "gateway": self.f["gateway"].get_text().strip(),
            }
            try:
                networkdiag.normalize_target(candidate)
                message = ""
            except ValueError as exc:
                message = "Fix before saving: %s." % str(exc)
        self.validation.set_text(message or "Ready to save.")
        self.save_button.set_sensitive(not message)
        return not message

    # VNC carries no device redirection, no audio and no domain login. Leaving
    # those controls active would imply settings that silently do nothing.
    RDP_ONLY = ("username", "domain", "password", "prompt_credentials",
                "gateway", "gateway_username", "gateway_domain", "app",
                "security", "cert_policy", "gfx",
                "audio_out", "audio_in", "redirect_clipboard",
                "redirect_usb_storage", "redirect_usb_devices",
                "redirect_smartcard", "redirect_printers")

    def _on_protocol_changed(self, widget=None):
        is_rdp = self.f["protocol"].get_active_id() != "vnc"
        for key in self.RDP_ONLY:
            if key in self.f:
                self.f[key].set_sensitive(is_rdp)
        # A protocol switch usually means the port is wrong for the new one.
        port = int(self.f["port"].get_value())
        if not is_rdp and port == 3389:
            self.f["port"].set_value(5900)
        elif is_rdp and port == 5900:
            self.f["port"].set_value(3389)

    def _reload_conn_list(self, select_id=None):
        for child in self.conn_list.get_children():
            self.conn_list.remove(child)
        for conn in self.result["connections"]:
            row = Gtk.ListBoxRow()
            row.conn_id = conn["id"]
            label = Gtk.Label(label=conn["name"] or conn["id"], xalign=0, margin=8)
            label.set_ellipsize(3)
            row.add(label)
            self.conn_list.add(row)
        self.conn_list.show_all()
        if select_id:
            for index, conn in enumerate(self.result["connections"]):
                if conn["id"] == select_id:
                    self._select_index(index)
                    return

    def _select_index(self, index):
        row = self.conn_list.get_row_at_index(index)
        if row is not None:
            self.conn_list.select_row(row)

    def _on_row_selected(self, _listbox, row):
        self._store_form()
        if row is None:
            self.current = None
            self.form.set_sensitive(False)
            return
        self.form.set_sensitive(True)
        self.current = row.conn_id
        self._load_form(tcconfig.find(self.result, row.conn_id))

    def _load_form(self, conn):
        if not conn:
            return
        self.f["name"].set_text(conn.get("name", ""))
        self.f["description"].set_text(conn.get("description", ""))
        self.f["group"].set_text(conn.get("group", "Connections"))
        self.f["host"].set_text(conn.get("host", ""))
        self.f["port"].set_value(int(conn.get("port", 3389)))
        self.f["username"].set_text(conn.get("username", ""))
        self.f["domain"].set_text(conn.get("domain", ""))
        self.f["password"].set_text(conn.get("password", ""))
        self.f["gateway"].set_text(conn.get("gateway", ""))
        self.f["gateway_username"].set_text(conn.get("gateway_username", ""))
        self.f["gateway_domain"].set_text(conn.get("gateway_domain", ""))
        self.f["app"].set_text(conn.get("app", ""))
        self.f["reconnect_delay"].set_value(int(conn.get("reconnect_delay", 5)))
        self.f["extra_args"].set_text(
            tcconfig.format_extra_args(conn.get("extra_args") or [])
        )

        display = (conn.get("display") or "fullscreen").lower()
        if display in ("fullscreen", "multimon", "window"):
            self.f["display"].set_active_id(display)
            self.f["display_custom"].set_text("")
        else:
            self.f["display"].set_active_id("custom")
            self.f["display_custom"].set_text(display)
        self._on_display_changed(self.f["display"])

        for key, default in (("cert_policy", "ignore"), ("security", "auto"),
                             ("gfx", "auto"), ("network", "auto"),
                             ("protocol", "rdp")):
            self.f[key].set_active_id(conn.get(key, default) or default)
        self._on_protocol_changed()

        for key in ("prompt_credentials", "audio_out", "audio_in", "redirect_clipboard",
                    "redirect_usb_storage", "redirect_usb_devices",
                    "redirect_smartcard", "redirect_printers", "auto_reconnect"):
            self.f[key].set_active(bool(conn.get(key)))
        self._validate_form()

    def _store_form(self):
        if not self.current:
            return
        conn = tcconfig.find(self.result, self.current)
        if not conn:
            return
        conn["name"] = self.f["name"].get_text().strip() or conn["id"]
        conn["description"] = self.f["description"].get_text().strip()
        conn["group"] = self.f["group"].get_text().strip() or "Connections"
        conn["host"] = self.f["host"].get_text().strip()
        conn["port"] = int(self.f["port"].get_value())
        conn["username"] = self.f["username"].get_text().strip()
        conn["domain"] = self.f["domain"].get_text().strip()
        conn["password"] = self.f["password"].get_text()
        conn["gateway"] = self.f["gateway"].get_text().strip()
        conn["gateway_username"] = self.f["gateway_username"].get_text().strip()
        conn["gateway_domain"] = self.f["gateway_domain"].get_text().strip()
        conn["app"] = self.f["app"].get_text().strip()
        conn["reconnect_delay"] = int(self.f["reconnect_delay"].get_value())
        conn["extra_args"] = tcconfig.parse_extra_args(self.f["extra_args"].get_text())

        mode = self.f["display"].get_active_id()
        conn["display"] = (self.f["display_custom"].get_text().strip() or "fullscreen") \
            if mode == "custom" else mode

        for key in ("cert_policy", "security", "gfx", "network", "protocol"):
            conn[key] = self.f[key].get_active_id()
        for key in ("prompt_credentials", "audio_out", "audio_in", "redirect_clipboard",
                    "redirect_usb_storage", "redirect_usb_devices",
                    "redirect_smartcard", "redirect_printers", "auto_reconnect"):
            conn[key] = self.f[key].get_active()

    def _on_add(self, *_):
        self._store_form()
        existing = {c["id"] for c in self.result["connections"]}
        index = 1
        while ("conn%d" % index) in existing:
            index += 1
        new = dict(tcconfig.CONNECTION_DEFAULTS)
        new.update({"id": "conn%d" % index, "name": "New connection", "extra_args": []})
        self.result["connections"].append(new)
        self._reload_conn_list(select_id=new["id"])

    def _on_copy(self, *_):
        self._store_form()
        source = tcconfig.find(self.result, self.current)
        if not source:
            return
        existing = {c["id"] for c in self.result["connections"]}
        index = 1
        while ("%s-copy%d" % (source["id"], index)) in existing:
            index += 1
        clone = copy.deepcopy(source)
        clone["id"] = "%s-copy%d" % (source["id"], index)
        clone["name"] = source["name"] + " (copy)"
        self.result["connections"].append(clone)
        self._reload_conn_list(select_id=clone["id"])

    def _on_remove(self, *_):
        if not self.current:
            return
        self.result["connections"] = [
            c for c in self.result["connections"] if c["id"] != self.current
        ]
        self.current = None
        self._reload_conn_list()
        self._select_index(0)

    # -------------------------------------------------------------- device --
    def _device_page(self):
        device = self.result["device"]
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        grid = FormGrid()
        self.d = {}

        self.d["hostname_prefix"] = grid.add_row(
            "Hostname prefix", Gtk.Entry(text=device.get("hostname_prefix", "thin")))
        self.d["keyboard_layout"] = grid.add_row(
            "Keyboard layout", combo_entry(KEYMAPS, device.get("keyboard_layout", "us")))
        self.d["keyboard_variant"] = grid.add_row(
            "Keyboard variant", Gtk.Entry(text=device.get("keyboard_variant", "")))
        self.d["timezone"] = grid.add_row(
            "Time zone", combo_entry(TIMEZONES, device.get("timezone", "UTC")))
        self.d["ntp_server"] = grid.add_row(
            "NTP server", Gtk.Entry(text=device.get("ntp_server", "pool.ntp.org")))
        self.d["ntp_server"].set_placeholder_text("your domain controller is the best choice")

        grid.add_heading("Display")
        self.d["resolution"] = grid.add_row(
            "Screen resolution", combo_entry(self._modes(), device.get("resolution", "auto")))
        self.d["screen_blank_minutes"] = grid.add_row(
            "Blank screen after (min, 0 = never)",
            Gtk.SpinButton.new_with_range(0, 180, 5))
        self.d["screen_blank_minutes"].set_value(int(device.get("screen_blank_minutes", 0)))

        grid.add_heading("Behaviour")
        auto_options = [("", "Show the connection list")] + \
                       [(c["id"], "Connect to %s" % c["name"]) for c in self.result["connections"]]
        self.d["auto_connect"] = grid.add_row(
            "At start-up", combo(auto_options, device.get("auto_connect", "")))
        self.d["allow_settings"] = grid.add_wide(
            Gtk.CheckButton(label="Allow users to open Settings"))
        self.d["allow_settings"].set_active(bool(device.get("allow_settings", True)))
        self.d["allow_console"] = grid.add_wide(
            Gtk.CheckButton(label="Allow switching to a text console (Ctrl+Alt+F1)"))
        self.d["allow_console"].set_active(bool(device.get("allow_console", False)))
        self.d["allow_terminal"] = grid.add_wide(
            Gtk.CheckButton(label="Allow Terminal in administrator tools"))
        self.d["allow_terminal"].set_active(bool(device.get("allow_terminal", True)))
        self.d["show_ip"] = grid.add_wide(
            Gtk.CheckButton(label="Show the IP address on screen"))
        self.d["show_ip"].set_active(bool(device.get("show_ip", True)))

        grid.add_heading("Administrator password")
        self.d["admin_password"] = grid.add_row("New password", Gtk.Entry(visibility=False))
        self.d["admin_password"].set_placeholder_text(
            "leave empty to keep the current password")
        clear = Gtk.CheckButton(label="Remove the password (Settings become unprotected)")
        self.d["admin_clear"] = grid.add_wide(clear)

        scroller.add(grid)
        return scroller

    def _modes(self):
        """Resolutions the primary output actually advertises."""
        modes = ["auto"]
        try:
            output = subprocess.run(["xrandr"], capture_output=True, text=True, timeout=10)
            for line in output.stdout.splitlines():
                parts = line.split()
                if parts and "x" in parts[0] and parts[0][0].isdigit():
                    if parts[0] not in modes:
                        modes.append(parts[0])
        except (OSError, subprocess.SubprocessError):
            pass
        return modes

    def _store_device(self):
        device = self.result["device"]
        device["hostname_prefix"] = self.d["hostname_prefix"].get_text().strip() or "thin"
        device["keyboard_layout"] = self.d["keyboard_layout"].get_child().get_text().strip() or "us"
        device["keyboard_variant"] = self.d["keyboard_variant"].get_text().strip()
        device["timezone"] = self.d["timezone"].get_child().get_text().strip() or "UTC"
        device["ntp_server"] = self.d["ntp_server"].get_text().strip()
        device["resolution"] = self.d["resolution"].get_child().get_text().strip() or "auto"
        device["screen_blank_minutes"] = int(self.d["screen_blank_minutes"].get_value())
        device["auto_connect"] = self.d["auto_connect"].get_active_id() or ""
        device["allow_settings"] = self.d["allow_settings"].get_active()
        device["allow_console"] = self.d["allow_console"].get_active()
        device["allow_terminal"] = self.d["allow_terminal"].get_active()
        device["show_ip"] = self.d["show_ip"].get_active()

        if self.d["admin_clear"].get_active():
            device["admin_password"] = ""
        else:
            new = self.d["admin_password"].get_text()
            if new:
                device["admin_password"] = tcconfig.hash_password(new)

    # --------------------------------------------------------- diagnostics --
    def _diagnostics_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=10)

        info = Gtk.Label(xalign=0)
        try:
            with open("/etc/thinclient/build-info", "r", encoding="utf-8") as fh:
                info.set_markup("<tt>%s</tt>" % GLib.markup_escape_text(fh.read().strip()))
        except OSError:
            info.set_text("build information unavailable")
        box.pack_start(info, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        self.logview = Gtk.TextView(editable=False, monospace=True)
        self.logview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        scroller.add(self.logview)
        box.pack_start(scroller, True, True, 0)

        row = Gtk.Box(spacing=6)
        for text, handler in (("Last session log", self._show_session_log),
                              ("System log", self._show_journal),
                              ("Network", self._show_network_diag)):
            btn = Gtk.Button(label=text)
            btn.connect("clicked", handler)
            row.pack_start(btn, False, False, 0)
        box.pack_start(row, False, False, 0)

        self._show_session_log()
        return box

    def _set_log(self, text):
        self.logview.get_buffer().set_text(text or "(empty)")

    def _show_session_log(self, *_):
        try:
            with open("/run/thinclient/last-session.log", "r", encoding="utf-8",
                      errors="replace") as fh:
                self._set_log(fh.read())
        except OSError:
            self._set_log("No session has been started since this client booted.")

    def _show_journal(self, *_):
        output = subprocess.run(
            ["journalctl", "-b", "--no-pager", "-n", "300"],
            capture_output=True, text=True, timeout=20,
        )
        self._set_log(output.stdout or output.stderr)

    def _show_network_diag(self, *_):
        chunks = []
        for argv in (["ip", "-brief", "address"], ["ip", "route"],
                     ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"],
                     ["cat", "/etc/resolv.conf"]):
            result = subprocess.run(argv, capture_output=True, text=True, timeout=15)
            chunks.append("$ %s\n%s" % (" ".join(argv), result.stdout or result.stderr))
        self._set_log("\n".join(chunks))

    # ------------------------------------------------------------ response --
    def _on_response(self, _dialog, response):
        if response == Gtk.ResponseType.OK:
            if not self._validate_form():
                self.stop_emission_by_name("response")
                return
            self._store_form()
            self._store_device()


# ----------------------------------------------------------------- network ---
class NetworkDialog(Gtk.Dialog):
    def __init__(self, parent, connections=None, test_only=False):
        super().__init__(title="Network test" if test_only else "Network",
                         transient_for=parent, modal=True)
        self.set_default_size(700, 520)
        self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.connections = list(connections or [])
        self.test_only = bool(test_only)
        self._test_generation = 0
        self._test_destroyed = False
        self.connect("destroy", self._on_test_destroyed)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)
        self.get_content_area().pack_start(box, True, True, 0)

        self.status = Gtk.Label(xalign=0)
        self.status.set_selectable(True)
        box.pack_start(self.status, False, False, 0)

        notebook = Gtk.Notebook()
        if not self.test_only:
            notebook.append_page(self._wired_page(), Gtk.Label(label="Wired"))
            notebook.append_page(self._wifi_page(), Gtk.Label(label="Wi-Fi"))
        notebook.append_page(self._test_page(), Gtk.Label(label="Test"))
        box.pack_start(notebook, True, True, 0)

        self.message = Gtk.Label(xalign=0)
        box.pack_start(self.message, False, False, 0)

        self.refresh()
        self.show_all()

    def _test_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=12)
        intro = Gtk.Label(
            label=("Check the route, DNS, selected server port, and RDP/VNC "
                   "handshake. Gateway ping is informational. No credentials "
                   "are sent."),
            xalign=0,
        )
        intro.set_line_wrap(True)
        box.pack_start(intro, False, False, 0)

        row = Gtk.Box(spacing=8)
        row.pack_start(Gtk.Label(label="Target", xalign=0), False, False, 0)
        self.test_target = Gtk.ComboBoxText()
        for connection in self.connections:
            host = connection.get("host") or "not configured"
            self.test_target.append(
                connection.get("id") or "",
                "%s — %s:%s" % (connection.get("name") or "Connection", host,
                                 connection.get("port") or "?"),
            )
        if self.connections:
            self.test_target.set_active(0)
        self.test_target.set_hexpand(True)
        row.pack_start(self.test_target, True, True, 0)
        box.pack_start(row, False, False, 0)

        self.test_spinner = Gtk.Spinner()
        self.test_run = Gtk.Button(label="Run Network Test")
        self.test_run.get_style_context().add_class("suggested-action")
        self.test_run.connect("clicked", self._run_network_test)
        self.test_run.set_sensitive(bool(self.connections))
        self.test_copy = Gtk.Button(label="Copy detailed report")
        self.test_copy.set_sensitive(False)
        self.test_copy.connect("clicked", self._copy_network_report)
        controls = Gtk.Box(spacing=8)
        controls.pack_start(self.test_run, False, False, 0)
        controls.pack_start(self.test_spinner, False, False, 0)
        controls.pack_end(self.test_copy, False, False, 0)
        box.pack_start(controls, False, False, 0)

        self.test_summary = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        initial = Gtk.Label(
            label=("Choose a configured connection, then run the test."
                   if self.connections else
                   "No configured connections are available to test."),
            xalign=0,
        )
        initial.set_line_wrap(True)
        self.test_summary.pack_start(initial, False, False, 4)
        box.pack_start(self.test_summary, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_min_content_height(150)
        self.test_report = Gtk.TextView(editable=False, monospace=True,
                                        cursor_visible=False)
        self.test_report.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.test_report.get_buffer().set_text(
            ("Choose a configured connection, then run the test."
             if self.connections else
             "No configured connections are available to test.")
        )
        scroller.add(self.test_report)
        self.test_details = Gtk.Expander(label="Technical details")
        self.test_details.add(scroller)
        box.pack_start(self.test_details, True, True, 0)
        return box

    def _selected_test_target(self):
        active = self.test_target.get_active_id()
        return next((connection for connection in self.connections
                     if connection.get("id") == active), {})

    def _run_network_test(self, *_):
        connection = self._selected_test_target()
        # Snapshot only what this credential-free probe needs. Never hand a
        # password, username, domain, or extra argument to the worker/report.
        target = {key: connection.get(key) for key in
                  ("name", "host", "port", "protocol", "gateway")}
        self._test_generation += 1
        generation = self._test_generation
        self.test_run.set_sensitive(False)
        self.test_copy.set_sensitive(False)
        self.test_spinner.start()
        self.test_report.get_buffer().set_text(
            "Testing local network, DNS, and %s:%s…" %
            (target.get("host") or "the selected server", target.get("port") or "?")
        )

        def worker():
            try:
                report = networkdiag.run_preflight(target)
            except Exception as exc:                # fail visibly, never freeze UI
                report = "Network diagnostics\nFAILED — %s" % str(exc)[:180]
            GLib.idle_add(self._finish_network_test, generation, report)

        threading.Thread(target=worker, daemon=True, name="network-preflight").start()

    def _finish_network_test(self, generation, report):
        if self._test_destroyed or generation != self._test_generation:
            return False
        self.test_report.get_buffer().set_text(report)
        if hasattr(self, "_render_network_summary"):
            self._render_network_summary(report)
        self.test_spinner.stop()
        self.test_run.set_sensitive(True)
        self.test_copy.set_sensitive(True)
        return False

    def _render_network_summary(self, report):
        for child in self.test_summary.get_children():
            self.test_summary.remove(child)
        rows = uxstate.parse_network_report(report)
        if not rows:
            label = Gtk.Label(label="The diagnostic report could not be summarized.",
                              xalign=0)
            self.test_summary.pack_start(label, False, False, 0)
        for item in rows:
            state = item["state"]
            marker = {"ok": "✓ PASS", "failed": "✕ FAILED",
                      "skipped": "• SKIPPED", "info": "• INFO"}.get(state, "• INFO")
            row = Gtk.Box(spacing=10)
            status = Gtk.Label(label=marker, xalign=0)
            status.set_width_chars(10)
            name = Gtk.Label(xalign=0)
            name.set_markup("<b>%s</b>" % GLib.markup_escape_text(item["label"]))
            detail = Gtk.Label(label=item.get("detail") or "", xalign=0)
            detail.set_ellipsize(3)
            detail.set_tooltip_text(item.get("detail") or "")
            row.pack_start(status, False, False, 0)
            row.pack_start(name, False, False, 0)
            row.pack_start(detail, True, True, 0)
            self.test_summary.pack_start(row, False, False, 0)
        self.test_summary.show_all()

    def _copy_network_report(self, *_):
        buffer_ = self.test_report.get_buffer()
        text = buffer_.get_text(buffer_.get_start_iter(), buffer_.get_end_iter(), True)
        Gtk.Clipboard.get_default(self.get_display()).set_text(text, -1)
        self.message.set_text("Network test report copied to the clipboard.")

    def _on_test_destroyed(self, *_):
        self._test_destroyed = True
        self._test_generation += 1

    def _nmcli(self, *args, timeout=45):
        return subprocess.run(["sudo", "-n", "/usr/bin/nmcli", *args],
                              capture_output=True, text=True, timeout=timeout)

    def refresh(self, *_):
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"],
                capture_output=True, text=True, timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            result = type("Result", (), {"stdout": "", "stderr": str(exc),
                                          "returncode": 1})()
        lines = []
        self.wired_devices = []
        for row in result.stdout.strip().splitlines():
            parts = tcconfig.parse_nmcli_terse(row)
            if len(parts) < 4 or parts[1] in ("loopback", "lo"):
                continue
            if parts[1] == "ethernet":
                self.wired_devices.append(parts[0])
            try:
                addr = subprocess.run(
                    ["nmcli", "-g", "IP4.ADDRESS,IP4.GATEWAY", "device", "show", parts[0]],
                    capture_output=True, text=True, timeout=15,
                ).stdout.split()
            except (OSError, subprocess.SubprocessError):
                addr = []
            lines.append("%-10s %-9s %-12s %s" % (parts[0], parts[1], parts[2],
                                                  " ".join(addr)))
        self.status.set_markup("<tt>%s</tt>" % GLib.markup_escape_text(
            "\n".join(lines) or "no network devices detected"))
        if hasattr(self, "wired_device"):
            active = self.wired_device.get_active_text()
            self.wired_device.remove_all()
            for device in self.wired_devices:
                self.wired_device.append_text(device)
            if active in self.wired_devices:
                self.wired_device.set_active(self.wired_devices.index(active))
            elif self.wired_devices:
                self.wired_device.set_active(0)

    def _wired_page(self):
        grid = FormGrid()
        self.wired_device = grid.add_row("Interface", Gtk.ComboBoxText())
        self.method = grid.add_row("Addressing",
                                   combo([("auto", "Automatic (DHCP)"),
                                          ("manual", "Static address")], "auto"))
        self.addr = grid.add_row("Address / prefix", Gtk.Entry())
        self.addr.set_placeholder_text("192.168.1.50/24")
        self.gw = grid.add_row("Gateway", Gtk.Entry())
        self.dns = grid.add_row("DNS servers", Gtk.Entry())
        self.dns.set_placeholder_text("192.168.1.1,192.168.1.2")

        row = Gtk.Box(spacing=6, margin_top=8)
        apply_btn = Gtk.Button(label="Apply")
        apply_btn.connect("clicked", self._apply_wired)
        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", self.refresh)
        row.pack_start(apply_btn, False, False, 0)
        row.pack_start(refresh_btn, False, False, 0)
        grid.add_wide(row)
        return grid

    def _apply_wired(self, *_):
        device = self.wired_device.get_active_text()
        if not device:
            return
        raw_name = subprocess.run(
            ["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", device],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        name_fields = tcconfig.parse_nmcli_terse(raw_name)
        name = name_fields[0] if name_fields else ""
        if not name or name == "--":
            connected = self._nmcli("device", "connect", device)
            if connected.returncode != 0:
                self.message.set_text(
                    connected.stderr.strip() or "Could not connect the interface."
                )
                return
            raw_name = subprocess.run(
                ["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", device],
                capture_output=True, text=True, timeout=15,
            ).stdout.strip()
            name_fields = tcconfig.parse_nmcli_terse(raw_name)
            name = name_fields[0] if name_fields else ""
        if not name or name == "--":
            self.message.set_text("No NetworkManager profile is bound to %s." % device)
            return

        if self.method.get_active_id() == "manual":
            address = self.addr.get_text().strip()
            if "/" not in address:
                self.message.set_text("Enter the address with a prefix, e.g. 192.168.1.50/24")
                return
            args = ["connection", "modify", name, "ipv4.method", "manual",
                    "ipv4.addresses", address,
                    "ipv4.gateway", self.gw.get_text().strip(),
                    "ipv4.dns", self.dns.get_text().strip()]
        else:
            args = ["connection", "modify", name, "ipv4.method", "auto",
                    "ipv4.addresses", "", "ipv4.gateway", "", "ipv4.dns", ""]

        result = self._nmcli(*args)
        if result.returncode != 0:
            self.message.set_text(result.stderr.strip() or "Could not apply the settings.")
            return
        activated = self._nmcli("connection", "up", name)
        if activated.returncode != 0:
            self.message.set_text(
                activated.stderr.strip() or "The profile was saved but could not be activated."
            )
            return
        self.message.set_text("Applied to %s." % name)
        GLib.timeout_add_seconds(2, lambda: (self.refresh(), False)[1])

    def _wifi_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)
        if not os.path.exists("/sbin/wpa_supplicant") and not os.path.exists("/usr/sbin/wpa_supplicant"):
            box.pack_start(Gtk.Label(label="Wi-Fi support is not installed in this image."),
                           False, False, 0)
            return box

        grid = FormGrid()
        self.ssid = grid.add_row("Network", Gtk.ComboBoxText.new_with_entry())
        self.wifi_pass = grid.add_row("Password", Gtk.Entry(visibility=False))
        row = Gtk.Box(spacing=6)
        scan = Gtk.Button(label="Scan")
        scan.connect("clicked", self._scan_wifi)
        join = Gtk.Button(label="Connect")
        join.connect("clicked", self._join_wifi)
        row.pack_start(scan, False, False, 0)
        row.pack_start(join, False, False, 0)
        grid.add_wide(row)
        box.pack_start(grid, False, False, 0)
        return box

    def _scan_wifi(self, *_):
        self._nmcli("device", "wifi", "rescan")
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"],
            capture_output=True, text=True, timeout=45,
        )
        self.ssid.remove_all()
        seen = set()
        for line in result.stdout.strip().splitlines():
            fields = tcconfig.parse_nmcli_terse(line)
            name = fields[0] if fields else ""
            if name and name not in seen:
                seen.add(name)
                self.ssid.append_text(name)
        self.message.set_text("Found %d network(s)." % len(seen))

    def _join_wifi(self, *_):
        name = self.ssid.get_child().get_text().strip()
        if not name:
            return
        args = ["device", "wifi", "connect", name]
        if self.wifi_pass.get_text():
            args += ["password", self.wifi_pass.get_text()]
        result = self._nmcli(*args, timeout=90)
        self.message.set_text(
            "Connected to %s." % name if result.returncode == 0
            else (result.stderr.strip() or "Could not join the network.")
        )
        self.refresh()
