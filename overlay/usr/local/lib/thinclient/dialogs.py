"""Support, administrator and connection dialogs for the appliance."""
import os
import platform
import shutil
import socket
import subprocess
import tempfile
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango
import uxstate
from uicommon import labelled, button, primary_ip, product_title

GITHUB_URL = "https://github.com/patnawa/Thinclient"
CHANGELOG_FILE = os.environ.get("TC_CHANGELOG_FILE", "/usr/share/thinclient/CHANGELOG.md")

class AboutDialog(Gtk.Dialog):
    """Product, support, and static device information in one calm view."""
    def __init__(self, parent, info, hardware):
        title = product_title(info)
        super().__init__(title="About %s" % title, transient_for=parent, modal=True)
        self.set_default_size(680, -1)
        self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.set_default_response(Gtk.ResponseType.CLOSE)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                          margin_start=24, margin_end=24,
                          margin_top=20, margin_bottom=16)
        content.pack_start(labelled(title, "tc-about-title"), False, False, 0)

        intro = labelled(
            "A focused Debian appliance for RDP, RemoteApp, and VNC sessions.",
            "tc-sub",
        )
        intro.set_line_wrap(True)
        content.pack_start(intro, False, False, 0)

        grid = Gtk.Grid(row_spacing=8, column_spacing=18, margin_top=4)
        grid.set_hexpand(True)
        content.pack_start(grid, False, False, 0)

        row = 0

        def section(text):
            nonlocal row
            label = labelled(text.upper(), "tc-about-section")
            grid.attach(label, 0, row, 2, 1)
            row += 1

        def detail(key, value):
            nonlocal row
            key_label = labelled(key, "tc-about-key")
            key_label.set_width_chars(13)
            value_label = labelled(value or "Unknown", "tc-about-value")
            value_label.set_selectable(True)
            value_label.set_ellipsize(Pango.EllipsizeMode.NONE)
            value_label.set_line_wrap(True)
            value_label.set_max_width_chars(58)
            value_label.set_hexpand(True)
            grid.attach(key_label, 0, row, 1, 1)
            grid.attach(value_label, 1, row, 1, 1)
            row += 1

        section("Release")
        detail("Version", info.get("version", "Unknown"))
        detail("System", info.get("base", "Unknown"))
        detail("Kernel", info.get("kernel") or platform.release())
        freerdp = (info.get("freerdp") or "Unknown").split("+")[0]
        detail("FreeRDP", freerdp)

        section("This device")
        for key in ("Architecture", "Processor", "Memory", "Graphics", "Network"):
            detail(key, hardware.get(key, "Unknown"))

        section("Project and support")
        link = Gtk.LinkButton.new_with_label(GITHUB_URL, GITHUB_URL)
        link.set_halign(Gtk.Align.START)
        grid.attach(Gtk.Label(label="GitHub", xalign=0), 0, row, 1, 1)
        grid.attach(link, 1, row, 1, 1)

        privacy = labelled(
            "Hardware is read locally once when About opens. Nothing is sent.",
            "tc-sub",
        )
        privacy.set_line_wrap(True)
        content.pack_start(privacy, False, False, 0)

        self.get_content_area().add(content)
        self.show_all()


class HelpDialog(Gtk.Dialog):
    """Public, credential-free device and support view."""

    NETWORK_TEST = 101
    CHANGELOG = 102

    def __init__(self, parent, info, hardware, cache, last_error=""):
        title = product_title(info)
        super().__init__(title="Help and device information", transient_for=parent,
                         modal=True)
        self.set_default_size(760, 620)
        self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.set_default_response(Gtk.ResponseType.CLOSE)
        hostname = socket.gethostname()
        address = primary_ip()
        self.report = uxstate.support_report(
            info, hardware, hostname, address, cache, last_error
        )
        self._qr_path = ""

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                          margin_start=24, margin_end=24,
                          margin_top=20, margin_bottom=16)
        content.pack_start(labelled("Help and device information", "tc-about-title"),
                           False, False, 0)
        intro = labelled(
            "Give this screen or its support report to the help desk. "
            "No usernames or passwords are included.", "tc-sub")
        intro.set_line_wrap(True)
        content.pack_start(intro, False, False, 0)

        summary = Gtk.Grid(row_spacing=9, column_spacing=18)
        summary.set_hexpand(True)
        details = [
            ("Version", info.get("version") or "Unknown"),
            ("Image profile", (info.get("profile") or cache.get("profile") or
                               "Unknown").title()),
            ("Device", hostname or "Unknown"),
            ("IP address", address or "No network"),
            ("Boot/cache", cache.get("summary") or "Unknown"),
            ("Last error", last_error or "None this boot"),
        ]
        config_status = uxstate.configuration_status()
        if config_status:
            details.append(("Configuration", "%s · %s" % (
                config_status.get("source", "Unknown"), config_status.get("state", "Unknown"))))
            details.append(("Last config update", config_status.get("last_success", "Unknown")))
        for row, (key, value) in enumerate(details):
            key_label = labelled(key, "tc-about-key")
            value_label = labelled(value, "tc-about-value")
            value_label.set_ellipsize(Pango.EllipsizeMode.NONE)
            value_label.set_line_wrap(True)
            value_label.set_selectable(True)
            summary.attach(key_label, 0, row, 1, 1)
            summary.attach(value_label, 1, row, 1, 1)
        content.pack_start(summary, False, False, 0)

        actions = Gtk.Box(spacing=8)
        copy_btn = button("Copy support report", ["tc-btn", "tc-primary"],
                          self._copy_report)
        network_btn = button("Run network test", ["tc-btn"],
                             lambda *_: self.response(self.NETWORK_TEST))
        changelog_btn = button("What's new", ["tc-btn"],
                               lambda *_: self.response(self.CHANGELOG))
        actions.pack_start(copy_btn, False, False, 0)
        actions.pack_start(network_btn, False, False, 0)
        actions.pack_start(changelog_btn, False, False, 0)
        content.pack_start(actions, False, False, 0)

        lower = Gtk.Box(spacing=18)
        expander = Gtk.Expander(label="Technical details")
        report_view = Gtk.TextView(editable=False, cursor_visible=False,
                                   monospace=True)
        report_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        report_view.get_buffer().set_text(self.report)
        report_scroll = Gtk.ScrolledWindow()
        report_scroll.set_min_content_height(190)
        report_scroll.add(report_view)
        expander.add(report_scroll)
        lower.pack_start(expander, True, True, 0)

        qr = self._make_qr(title, hostname, address, cache, last_error)
        if qr:
            qr_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            qr_box.pack_start(Gtk.Image.new_from_file(qr), False, False, 0)
            qr_box.pack_start(labelled("Scan for a compact support code", "tc-sub"),
                              False, False, 0)
            lower.pack_end(qr_box, False, False, 0)
        content.pack_start(lower, True, True, 0)

        privacy = Gtk.Label(
            label="Hardware is read locally. Nothing is automatically uploaded.",
            xalign=0,
        )
        privacy.get_style_context().add_class("tc-sub")
        content.pack_end(privacy, False, False, 0)
        self.get_content_area().add(content)
        self.connect("destroy", self._cleanup_qr)
        self.show_all()

    def _copy_report(self, *_):
        Gtk.Clipboard.get_default(self.get_display()).set_text(self.report, -1)

    def _make_qr(self, title, hostname, address, cache, last_error):
        encoder = shutil.which("qrencode")
        if not encoder:
            return ""
        payload = " | ".join((
            uxstate.clean_text(title, "ThinClient", 40),
            "device=" + uxstate.clean_text(hostname, "unknown", 40),
            "ip=" + uxstate.clean_text(address, "none", 48),
            "boot=" + uxstate.clean_text(cache.get("summary"), "unknown", 80),
            "error=" + uxstate.clean_text(last_error, "none", 80),
        ))
        try:
            handle = tempfile.NamedTemporaryFile(
                prefix="thinclient-support-", suffix=".png", delete=False)
            handle.close()
            result = subprocess.run(
                [encoder, "-o", handle.name, "-s", "3", "--", payload],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0 and os.path.getsize(handle.name) > 0:
                self._qr_path = handle.name
                return handle.name
            os.unlink(handle.name)
        except (OSError, subprocess.SubprocessError):
            pass
        return ""

    def _cleanup_qr(self, *_):
        if self._qr_path:
            try:
                os.unlink(self._qr_path)
            except OSError:
                pass
            self._qr_path = ""


class ChangelogDialog(Gtk.Dialog):
    """Offline release history shipped inside every image."""

    def __init__(self, parent, path=CHANGELOG_FILE):
        super().__init__(title="What's new", transient_for=parent, modal=True)
        self.set_default_size(760, 620)
        self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.set_default_response(Gtk.ResponseType.CLOSE)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                          margin_start=22, margin_end=22,
                          margin_top=18, margin_bottom=14)
        content.pack_start(labelled("What's new in ThinClient", "tc-about-title"),
                           False, False, 0)
        intro = labelled(
            "These release notes are stored in the image and work without a network.",
            "tc-sub",
        )
        intro.set_line_wrap(True)
        content.pack_start(intro, False, False, 0)

        view = Gtk.TextView(editable=False, cursor_visible=False, monospace=False)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.set_left_margin(12)
        view.set_right_margin(12)
        view.set_top_margin(10)
        view.set_bottom_margin(10)
        view.set_hexpand(True)
        view.set_vexpand(True)
        view.get_buffer().set_text(uxstate.changelog_text(path))
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(420)
        scroller.add(view)
        content.pack_start(scroller, True, True, 0)
        self.get_content_area().add(content)
        self.show_all()


class AdminDialog(Gtk.Dialog):
    """One protected place for settings and technician tools."""

    SETTINGS = 201
    NETWORK = 202
    TERMINAL = 203

    def __init__(self, parent, allow_settings=True, allow_terminal=True):
        super().__init__(title="Administrator tools", transient_for=parent, modal=True)
        self.set_default_size(560, -1)
        self.add_button("Close", Gtk.ResponseType.CLOSE)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=20)
        intro = Gtk.Label(
            label="Configuration and support tools for an authorised administrator.",
            xalign=0,
        )
        intro.set_line_wrap(True)
        box.pack_start(intro, False, False, 0)
        for title, description, response, enabled in (
            ("Settings", "Connections, display, device, and policy",
             self.SETTINGS, allow_settings),
            ("Network", "Wired/Wi-Fi configuration and detailed tests",
             self.NETWORK, True),
            ("Terminal", "Open a local support shell",
             self.TERMINAL, allow_terminal),
        ):
            row = Gtk.Button()
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin=8)
            inner.pack_start(labelled(title, "tc-conn-name"), False, False, 0)
            inner.pack_start(labelled(description, "tc-conn-desc"), False, False, 0)
            row.add(inner)
            row.set_sensitive(enabled)
            row.connect("clicked", lambda _button, value=response: self.response(value))
            box.pack_start(row, False, False, 0)
        self.get_content_area().add(box)
        self.show_all()


class ConnectionProgressDialog(Gtk.Dialog):
    """Visible, cancellable progress while the endpoint is being checked."""

    def __init__(self, parent, connection, cancel_handler):
        super().__init__(title="Connecting", transient_for=parent, modal=True)
        self.set_deletable(False)
        self.set_default_size(500, -1)
        self.cancel_button = self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=22)
        box.pack_start(labelled("Connecting to %s" % connection["name"],
                                "tc-progress-title"), False, False, 0)
        self.stage = labelled("Preparing connection", "tc-progress-stage")
        self.detail = labelled("Checking local settings…", "tc-sub")
        self.detail.set_line_wrap(True)
        self.progress = Gtk.ProgressBar(show_text=False)
        box.pack_start(self.stage, False, False, 0)
        box.pack_start(self.detail, False, False, 0)
        box.pack_start(self.progress, False, False, 0)
        self.get_content_area().add(box)
        self._pulse_id = GLib.timeout_add(120, self._pulse)
        self.connect("response", lambda _dialog, response:
                     cancel_handler() if response == Gtk.ResponseType.CANCEL else None)
        self.connect("destroy", self._destroyed)
        self.show_all()

    def _pulse(self):
        self.progress.pulse()
        return True

    def set_stage(self, stage, detail=""):
        self.stage.set_text(stage)
        self.detail.set_text(detail)

    def set_cancelling(self):
        self.set_stage("Cancelling", "Waiting for the connection attempt to stop…")
        self.cancel_button.set_sensitive(False)

    def _destroyed(self, *_):
        if self._pulse_id:
            GLib.source_remove(self._pulse_id)
            self._pulse_id = None


class ConnectionErrorDialog(Gtk.Dialog):
    """Actionable failure instead of a technical status-line dead end."""

    NETWORK_TEST = 301

    def __init__(self, parent, connection, message, retryable=True):
        super().__init__(title="Could not connect", transient_for=parent, modal=True)
        self.set_default_size(600, -1)
        self.add_button("Choose another", Gtk.ResponseType.CANCEL)
        self.add_button("Run network test", self.NETWORK_TEST)
        if retryable:
            retry = self.add_button("Try again", Gtk.ResponseType.OK)
            retry.get_style_context().add_class("suggested-action")
            self.set_default_response(Gtk.ResponseType.OK)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=22)
        box.pack_start(labelled("Connection to %s failed" % connection["name"],
                                "tc-progress-title"), False, False, 0)
        detail = labelled(message, "tc-error-detail")
        detail.set_ellipsize(Pango.EllipsizeMode.NONE)
        detail.set_line_wrap(True)
        box.pack_start(detail, False, False, 0)
        hint = labelled(
            "Try the network test for a safe route, DNS, port, and protocol check. "
            "No credentials are sent by that test.", "tc-sub")
        hint.set_ellipsize(Pango.EllipsizeMode.NONE)
        hint.set_line_wrap(True)
        box.pack_start(hint, False, False, 0)
        self.get_content_area().add(box)
        self.show_all()


# ------------------------------------------------------- credential prompt ---
class CredentialDialog(Gtk.Dialog):
    def __init__(self, parent, conn):
        super().__init__(title="Sign in to %s" % conn["name"], transient_for=parent,
                         modal=True)
        self.set_default_size(480, -1)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.connect_button = self.add_button("Connect", Gtk.ResponseType.OK)
        self.connect_button.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.OK)

        grid = Gtk.Grid(row_spacing=8, column_spacing=10, margin=18)
        self.user = Gtk.Entry(text=conn.get("username", ""), activates_default=True)
        self.domain = Gtk.Entry(text=conn.get("domain", ""), activates_default=True)
        self.password = Gtk.Entry(visibility=False, activates_default=True)
        self.remember = Gtk.CheckButton(label="Remember for this session")
        self.show_password = Gtk.CheckButton(label="Show password")
        self.show_password.connect(
            "toggled", lambda widget: self.password.set_visibility(widget.get_active()))
        self.caps = Gtk.Label(xalign=0)
        self.caps.get_style_context().add_class("tc-status-bad")

        for row, (text, widget) in enumerate((
            ("Username", self.user), ("Domain", self.domain), ("Password", self.password)
        )):
            grid.attach(Gtk.Label(label=text, xalign=1), 0, row, 1, 1)
            widget.set_hexpand(True)
            grid.attach(widget, 1, row, 1, 1)
        options = Gtk.Box(spacing=14)
        options.pack_start(self.remember, False, False, 0)
        options.pack_start(self.show_password, False, False, 0)
        grid.attach(options, 1, 3, 1, 1)
        grid.attach(self.caps, 1, 4, 1, 1)

        # This server checks credentials before it will show a desktop, so a
        # blank username or password cannot succeed. FreeRDP's own response to
        # missing credentials is to try to prompt on a terminal that does not
        # exist and abort with "the connection was cancelled", which tells the
        # person at the screen nothing. Refuse to start instead.
        self.hint = Gtk.Label(xalign=0)
        self.hint.get_style_context().add_class("tc-sub")
        grid.attach(self.hint, 1, 5, 1, 1)

        for entry in (self.user, self.password):
            entry.connect("changed", self._validate)
        self.password.connect("key-release-event", self._caps_state)

        self.get_content_area().add(grid)
        self.show_all()
        self._validate()
        self._caps_state()
        self.password.grab_focus() if conn.get("username") else self.user.grab_focus()

    def _validate(self, *_):
        missing = []
        if not self.user.get_text().strip():
            missing.append("username")
        if not self.password.get_text():
            missing.append("password")
        self.connect_button.set_sensitive(not missing)
        self.hint.set_text(("Enter a %s to continue." % " and ".join(missing))
                           if missing else "")

    def _caps_state(self, *_):
        keymap = Gdk.Keymap.get_default()
        active = bool(keymap and keymap.get_caps_lock_state())
        self.caps.set_text("Caps Lock is on" if active else "")
        return False

    def values(self):
        return (self.user.get_text().strip(), self.domain.get_text().strip(),
                self.password.get_text(), self.remember.get_active())
