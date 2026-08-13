#!/usr/bin/env python3
"""ThinClient connection manager.

A fullscreen GTK front end: pick a server, get an RDP session, come back here
when it ends. Everything an on-site user is allowed to touch lives in this one
window; everything else is behind the admin password.
"""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa: E402

import os  # noqa: E402
import shutil  # noqa: E402
import signal  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

sys.path.insert(0, "/usr/local/lib/thinclient")
import tcconfig  # noqa: E402
from tcconfig import verify_password  # noqa: E402

SESSION_LOG = "/run/thinclient/last-session.log"
BUILD_INFO = "/etc/thinclient/build-info"
SESSION_BAR = "/usr/local/lib/thinclient/sessionbar.py"
DISCONNECT_MARKER = "/run/thinclient/disconnect-requested"

CSS = b"""
window, .tc-root            { background-color: #16191d; }
.tc-header                  { background-color: #0f1215; padding: 14px 22px; }
.tc-title                   { color: #ffffff; font-size: 19px; font-weight: bold; }
.tc-sub                     { color: #7f8b99; font-size: 12px; }
.tc-clock                   { color: #d7dde4; font-size: 17px; font-weight: bold; }
.tc-status                  { color: #8b97a5; font-size: 12px; padding: 6px 22px; }
.tc-status-bad              { color: #ff8a80; font-size: 12px; padding: 6px 22px; }
.tc-listlabel               { color: #6f7b8a; font-size: 11px; letter-spacing: 1px; }
list.tc-list                { background-color: transparent; }
list.tc-list row            { background-color: #1f242a; border-radius: 8px;
                              margin: 5px 0px; padding: 14px 18px; }
list.tc-list row:selected    { background-color: #2f6fd0; }
.tc-conn-name               { color: #ffffff; font-size: 16px; font-weight: bold; }
.tc-conn-host               { color: #93a0ae; font-size: 12px; }
list.tc-list row:selected .tc-conn-host { color: #d8e6f7; }
.tc-bar                     { background-color: #0f1215; padding: 12px 22px; }
button.tc-btn               { background-image: none; background-color: #262d35;
                              color: #e6ebf0; border: 1px solid #333c46;
                              border-radius: 6px; padding: 10px 18px; font-size: 14px; }
button.tc-btn:hover         { background-color: #313a44; }
button.tc-primary           { background-color: #2f6fd0; color: #ffffff;
                              border-color: #2f6fd0; font-weight: bold; }
button.tc-primary:hover     { background-color: #3d80e6; }
button.tc-danger:hover      { background-color: #a8322c; border-color: #a8322c; color: #fff; }
.tc-empty                   { color: #7f8b99; font-size: 14px; }
"""


# ----------------------------------------------------------------- helpers ---
def run(argv, timeout=15):
    """Run a command, return stdout ('' on any failure)."""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return result.stdout if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def primary_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))          # TEST-NET-1: routes, never answers
        return sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()


def build_info():
    info = {}
    try:
        with open(BUILD_INFO, "r", encoding="utf-8") as fh:
            for line in fh:
                if "=" in line:
                    key, value = line.strip().split("=", 1)
                    info[key] = value
    except OSError:
        pass
    return info


def labelled(text, css_class):
    label = Gtk.Label(label=text, xalign=0)
    label.get_style_context().add_class(css_class)
    label.set_ellipsize(Pango.EllipsizeMode.END)
    return label


def button(text, css_classes, handler):
    btn = Gtk.Button(label=text)
    for cls in css_classes:
        btn.get_style_context().add_class(cls)
    btn.connect("clicked", handler)
    return btn


# ------------------------------------------------------- credential prompt ---
class CredentialDialog(Gtk.Dialog):
    def __init__(self, parent, conn):
        super().__init__(title="Sign in to %s" % conn["name"], transient_for=parent,
                         modal=True)
        self.set_default_size(420, -1)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.connect_button = self.add_button("Connect", Gtk.ResponseType.OK)
        self.connect_button.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.OK)

        grid = Gtk.Grid(row_spacing=8, column_spacing=10, margin=18)
        self.user = Gtk.Entry(text=conn.get("username", ""), activates_default=True)
        self.domain = Gtk.Entry(text=conn.get("domain", ""), activates_default=True)
        self.password = Gtk.Entry(visibility=False, activates_default=True)
        self.remember = Gtk.CheckButton(label="Remember for this session")

        for row, (text, widget) in enumerate((
            ("Username", self.user), ("Domain", self.domain), ("Password", self.password)
        )):
            grid.attach(Gtk.Label(label=text, xalign=1), 0, row, 1, 1)
            widget.set_hexpand(True)
            grid.attach(widget, 1, row, 1, 1)
        grid.attach(self.remember, 1, 3, 1, 1)

        # This server checks credentials before it will show a desktop, so a
        # blank username or password cannot succeed. FreeRDP's own response to
        # missing credentials is to try to prompt on a terminal that does not
        # exist and abort with "the connection was cancelled", which tells the
        # person at the screen nothing. Refuse to start instead.
        self.hint = Gtk.Label(xalign=0)
        self.hint.get_style_context().add_class("tc-sub")
        grid.attach(self.hint, 1, 4, 1, 1)

        for entry in (self.user, self.password):
            entry.connect("changed", self._validate)

        self.get_content_area().add(grid)
        self.show_all()
        self._validate()
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

    def values(self):
        return (self.user.get_text().strip(), self.domain.get_text().strip(),
                self.password.get_text(), self.remember.get_active())


# --------------------------------------------------------------- main window -
class ThinClient(Gtk.Window):
    def __init__(self):
        super().__init__(title="ThinClient")
        self.cfg = tcconfig.load()
        self.info = build_info()
        self.session_active = False
        self.cancel_reconnect = False
        self._countdown_id = None

        self.get_style_context().add_class("tc-root")
        self.set_default_size(900, 640)
        self.connect("destroy", Gtk.main_quit)
        self.connect("key-press-event", self.on_key)
        self.fullscreen()

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(outer)
        outer.pack_start(self._header(), False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                       margin_start=22, margin_end=22, margin_top=14)
        outer.pack_start(body, True, True, 0)
        body.pack_start(labelled("AVAILABLE CONNECTIONS", "tc-listlabel"), False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.listbox = Gtk.ListBox()
        self.listbox.get_style_context().add_class("tc-list")
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-activated", lambda *_: self.start_session())
        scroller.add(self.listbox)
        body.pack_start(scroller, True, True, 0)

        self.status = labelled("", "tc-status")
        outer.pack_start(self.status, False, False, 0)
        outer.pack_start(self._toolbar(), False, False, 0)

        self.refresh_list()
        GLib.timeout_add_seconds(1, self.tick)
        GLib.timeout_add_seconds(5, self.refresh_status)
        self.refresh_status()

    # ------------------------------------------------------------- chrome ---
    def _header(self):
        box = Gtk.Box(spacing=12)
        box.get_style_context().add_class("tc-header")

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left.pack_start(labelled(self.info.get("name", "ThinClient"), "tc-title"), False, False, 0)
        self.subtitle = labelled("", "tc-sub")
        left.pack_start(self.subtitle, False, False, 0)
        box.pack_start(left, True, True, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.clock = Gtk.Label(xalign=1)
        self.clock.get_style_context().add_class("tc-clock")
        right.pack_start(self.clock, False, False, 0)
        self.netlabel = Gtk.Label(xalign=1)
        self.netlabel.get_style_context().add_class("tc-sub")
        right.pack_start(self.netlabel, False, False, 0)
        box.pack_end(right, False, False, 0)
        return box

    def _toolbar(self):
        bar = Gtk.Box(spacing=10)
        bar.get_style_context().add_class("tc-bar")

        self.connect_btn = button("Connect", ["tc-btn", "tc-primary"],
                                  lambda *_: self.start_session())
        bar.pack_start(self.connect_btn, False, False, 0)
        bar.pack_start(button("Settings", ["tc-btn"], self.on_settings), False, False, 0)
        bar.pack_start(button("Network", ["tc-btn"], self.on_network), False, False, 0)
        self.terminal_btn = button("Terminal", ["tc-btn"], self.on_terminal)
        bar.pack_start(self.terminal_btn, False, False, 0)
        bar.pack_end(button("Shut Down", ["tc-btn", "tc-danger"],
                            lambda *_: self.power_off("poweroff")), False, False, 0)
        bar.pack_end(button("Restart", ["tc-btn"],
                            lambda *_: self.power_off("reboot")), False, False, 0)
        return bar

    # -------------------------------------------------------------- state ---
    def refresh_list(self):
        for child in self.listbox.get_children():
            self.listbox.remove(child)

        if not self.cfg["connections"]:
            row = Gtk.ListBoxRow(activatable=False, selectable=False)
            row.add(labelled("No connections configured. Open Settings to add one.",
                             "tc-empty"))
            self.listbox.add(row)
        else:
            for conn in self.cfg["connections"]:
                row = Gtk.ListBoxRow()
                row.conn_id = conn["id"]
                inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                inner.pack_start(labelled(conn["name"], "tc-conn-name"), False, False, 0)
                detail = "%s:%d" % (conn["host"], conn["port"])
                if conn.get("domain"):
                    detail += "   %s\\%s" % (conn["domain"], conn.get("username") or "?")
                elif conn.get("username"):
                    detail += "   %s" % conn["username"]
                inner.pack_start(labelled(detail, "tc-conn-host"), False, False, 0)
                row.add(inner)
                self.listbox.add(row)

        self.listbox.show_all()
        first = self.listbox.get_row_at_index(0)
        if first is not None and getattr(first, "conn_id", None):
            self.listbox.select_row(first)

    def selected_connection(self):
        row = self.listbox.get_selected_row()
        if row is None or not getattr(row, "conn_id", None):
            return None
        return tcconfig.find(self.cfg, row.conn_id)

    def tick(self):
        self.clock.set_text(time.strftime("%H:%M:%S"))
        return True

    def reload_config(self):
        """Re-read every layer. Called on SIGHUP when central config arrives."""
        if self.session_active:
            return True
        self.cfg = tcconfig.load()
        self.refresh_list()
        self.set_status("Configuration updated.")
        return True

    def refresh_status(self):
        host = socket.gethostname()
        addr = primary_ip() if self.cfg["device"].get("show_ip", True) else ""
        self.netlabel.set_text("%s    %s" % (host, addr or "no network"))
        self.subtitle.set_text(
            "%s   FreeRDP %s" % (self.info.get("base", ""),
                                 self.info.get("freerdp", "").split("+")[0])
        )
        return True

    def set_status(self, text, bad=False):
        ctx = self.status.get_style_context()
        ctx.remove_class("tc-status")
        ctx.remove_class("tc-status-bad")
        ctx.add_class("tc-status-bad" if bad else "tc-status")
        self.status.set_text(text)

    # ------------------------------------------------------------ session ---
    def start_session(self, conn=None):
        if self.session_active:
            return
        conn = conn or self.selected_connection()
        if conn is None:
            self.set_status("Select a connection first.", bad=True)
            return
        if not conn["host"]:
            self.set_status("This connection has no server address.", bad=True)
            return

        password = conn.get("password", "")
        # VNC has no domain login and classic VNC auth has no username at all,
        # so there is nothing for our credential dialog to collect - the viewer
        # asks for the password itself.
        is_vnc = (conn.get("protocol") or "rdp").lower() == "vnc"
        needs_prompt = (not is_vnc) and conn.get("prompt_credentials", True) and (
            not conn.get("username") or not password
        )
        if needs_prompt:
            dialog = CredentialDialog(self, conn)
            response = dialog.run()
            user, domain, password, remember = dialog.values()
            dialog.destroy()
            if response != Gtk.ResponseType.OK:
                return
            conn = dict(conn)
            conn["username"], conn["domain"] = user, domain
            # Keep it on this in-memory copy so an auto-reconnect does not stop
            # to ask again. It is only written to disk if "remember" was ticked.
            conn["password"] = password
            if remember:
                stored = tcconfig.find(self.cfg, conn["id"])
                if stored:
                    stored["username"], stored["domain"] = user, domain
                    stored["password"] = password
                self.refresh_list()

        # Belt and braces: a stored configuration with a username but no
        # password reaches here without the dialog ever being shown.
        if not is_vnc and (not conn.get("username") or not password):
            self.set_status(
                "%s needs a username and password before it will connect."
                % conn["name"], bad=True)
            return

        self.session_active = True
        self.cancel_reconnect = False
        self.connect_btn.set_sensitive(False)
        self.set_status("Connecting to %s ..." % conn["host"])
        self.hide()
        while Gtk.events_pending():
            Gtk.main_iteration()

        threading.Thread(target=self._session_worker, args=(conn, password),
                         daemon=True).start()

    def _session_worker(self, conn, password):
        # Whatever happens in here, _session_done must run: it is the only thing
        # that brings the window back. A thread dying quietly would leave the
        # client on a blank screen with no way out.
        try:
            code, error = self._run_session(conn, password)
        except Exception as exc:                       # noqa: BLE001 - last resort
            code, error = -1, str(exc)
        GLib.idle_add(self._session_done, conn, code, error)

    def _run_session(self, conn, password):
        """Launch FreeRDP and wait for it. Returns (exit_code, error_or_None)."""
        debug = os.path.exists("/run/thinclient/debug")
        try:
            argv, stdin_text = tcconfig.build_command(
                conn, self.cfg["device"], password=password, debug=debug
            )
        except (RuntimeError, OSError) as exc:
            return -1, str(exc)

        try:
            os.makedirs("/run/thinclient", exist_ok=True)
            with open(SESSION_LOG, "w", encoding="utf-8") as log:
                log.write("$ %s\n\n" % " ".join(
                    a if not a.startswith("/p:") else "/p:***" for a in argv))
                log.flush()
                proc = subprocess.Popen(
                    argv,
                    stdin=subprocess.PIPE if stdin_text else subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT,
                    env=tcconfig.prepare_environment(conn),
                )
                if stdin_text:
                    try:
                        proc.stdin.write(stdin_text.encode())
                        proc.stdin.flush()
                    except OSError:
                        pass
                    finally:
                        proc.stdin.close()

                # A full-screen session hides everything, so give the user a
                # visible way back. The bar floats above the session and exits
                # on its own when the session ends.
                bar = None
                if self.cfg["device"].get("session_bar", True) \
                        and os.path.exists(SESSION_BAR):
                    try:
                        bar = subprocess.Popen(
                            ["python3", SESSION_BAR, str(proc.pid), conn["name"]],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        )
                    except OSError:
                        bar = None

                code = proc.wait()
                if bar and bar.poll() is None:
                    bar.terminate()
        except OSError as exc:
            return -1, str(exc)

        # The bar records a deliberate disconnect before it kills FreeRDP.
        # Without this the manager would see a non-zero exit, call it a dropped
        # session and start counting down to reconnect the user to the thing
        # they just chose to leave.
        if os.path.exists(DISCONNECT_MARKER):
            try:
                os.remove(DISCONNECT_MARKER)
            except OSError:
                pass
            return 0, None
        return code, None

    def _session_done(self, conn, code, error):
        self.session_active = False
        self.connect_btn.set_sensitive(True)
        self.show_all()
        self.present()

        if error:
            self.set_status("Could not start the session: %s" % error, bad=True)
            return
        if code == 0:
            self.set_status("Session to %s ended." % conn["name"])
            return

        failure = tcconfig.explain_failure(SESSION_LOG, code)
        self.set_status("%s — %s" % (conn["name"], failure.message), bad=True)

        if conn.get("auto_reconnect") and failure.retryable \
                and not self.cancel_reconnect and not self._countdown_id:
            self._reconnect_countdown(conn, max(2, int(conn.get("reconnect_delay", 5))))
        return False

    def _reconnect_countdown(self, conn, seconds):
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text="Connection to %s was lost" % conn["name"],
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Reconnect now", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        remaining = {"n": seconds}

        def update():
            if remaining["n"] <= 0:
                dialog.response(Gtk.ResponseType.OK)
                return False
            dialog.format_secondary_text(
                "Reconnecting in %d second%s..."
                % (remaining["n"], "" if remaining["n"] == 1 else "s")
            )
            remaining["n"] -= 1
            return True

        update()
        self._countdown_id = GLib.timeout_add_seconds(1, update)
        response = dialog.run()
        if self._countdown_id:
            GLib.source_remove(self._countdown_id)
            self._countdown_id = None
        dialog.destroy()

        if response == Gtk.ResponseType.OK:
            self.start_session(conn)
        else:
            self.cancel_reconnect = True

    # -------------------------------------------------------------- admin ---
    def authorised(self):
        stored = self.cfg["device"].get("admin_password", "")
        if not stored:
            return True
        dialog = Gtk.Dialog(title="Administrator", transient_for=self, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Unlock", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        entry = Gtk.Entry(visibility=False, activates_default=True, margin=18)
        entry.set_placeholder_text("Administrator password")
        dialog.get_content_area().add(entry)
        dialog.show_all()
        ok = dialog.run() == Gtk.ResponseType.OK and verify_password(stored, entry.get_text())
        dialog.destroy()
        if not ok:
            self.set_status("Incorrect administrator password.", bad=True)
        return ok

    def on_settings(self, *_):
        # PyGObject swallows exceptions raised inside a signal handler - it
        # prints a traceback to stderr and returns. On a kiosk with no visible
        # stderr that reads as "the button does nothing", so report failures
        # where the user can actually see them.
        try:
            self._open_settings()
        except Exception as exc:                       # noqa: BLE001 - last resort
            self.set_status("Settings failed: %s" % exc, bad=True)

    def _open_settings(self):
        if not self.cfg["device"].get("allow_settings", True):
            self.set_status("Settings are disabled on this device.", bad=True)
            return
        if not self.authorised():
            return
        from settings import SettingsDialog          # noqa: WPS433 - kept off the boot path
        dialog = SettingsDialog(self, self.cfg)
        accepted = dialog.run() == Gtk.ResponseType.OK
        result = dialog.result
        dialog.destroy()
        if not accepted:
            return

        # Apply to the running UI first. Whether the settings can be written to
        # storage is a separate question, and a storage failure must never make
        # an edit look like it was ignored.
        self.cfg = result
        self.refresh_list()

        ok, message = tcconfig.save(self.cfg)
        self.set_status(message, bad=not ok)
        self.run_privileged(["/usr/local/sbin/tc-apply-config"], wait=False)

    def on_terminal(self, *_):
        """Open a terminal for on-site support.

        Behind the administrator password, because a shell is the one thing on
        this appliance that leads anywhere else.
        """
        try:
            if not self.cfg["device"].get("allow_terminal", True):
                self.set_status("The terminal is disabled on this device.", bad=True)
                return
            if not self.authorised():
                return

            emulator = None
            for candidate in ("xterm", "x-terminal-emulator"):
                if shutil.which(candidate):
                    emulator = candidate
                    break
            if not emulator:
                self.set_status(
                    "No terminal is installed in this image "
                    "(build with INCLUDE_ADMIN_TOOLS=1).", bad=True)
                return

            subprocess.Popen([
                emulator, "-title", "ThinClient terminal",
                "-geometry", "110x34",
                "-fa", "Monospace", "-fs", "11",
                "-bg", "#16191d", "-fg", "#d7dde4",
                "-sb", "-sl", "5000",
            ])
            self.set_status(
                "Terminal opened. Close it to return; the session is unaffected.")
        except Exception as exc:                     # noqa: BLE001 - last resort
            self.set_status("Could not open a terminal: %s" % exc, bad=True)

    def on_network(self, *_):
        try:
            from settings import NetworkDialog       # noqa: WPS433
            dialog = NetworkDialog(self)
            dialog.run()
            dialog.destroy()
            self.refresh_status()
        except Exception as exc:                     # noqa: BLE001 - last resort
            self.set_status("Network settings failed: %s" % exc, bad=True)

    def run_privileged(self, argv, wait=True):
        """Run a command via sudo. Returns (ok, message) - never raises.

        Falls back to running directly when we are already root, which is what
        happens when the manager is started by hand for testing.
        """
        attempts = [["sudo", "-n"] + argv]
        if os.geteuid() == 0:
            attempts.insert(0, argv)
        last = "no command could be run"
        for attempt in attempts:
            try:
                if not wait:
                    subprocess.Popen(attempt)
                    return True, ""
                done = subprocess.run(attempt, capture_output=True, text=True, timeout=30)
                if done.returncode == 0:
                    return True, ""
                last = (done.stderr or done.stdout or "").strip().splitlines()
                last = last[-1] if last else "exit status %d" % done.returncode
            except FileNotFoundError:
                last = "%s is not installed" % attempt[0]
            except (OSError, subprocess.SubprocessError) as exc:
                last = str(exc)
        return False, last

    def power_off(self, action):
        verb = "restart" if action == "reboot" else "shut down"
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL, text="Really %s this client?" % verb,
        )
        dialog.set_default_response(Gtk.ResponseType.OK)   # keyboard-only clients
        confirmed = dialog.run() == Gtk.ResponseType.OK
        dialog.destroy()
        if not confirmed:
            return

        self.set_status("Asking the system to %s..." % verb)
        while Gtk.events_pending():
            Gtk.main_iteration()
        ok, message = self.run_privileged(["/usr/bin/systemctl", action])
        if not ok:
            self.set_status("Could not %s: %s" % (verb, message), bad=True)

    def on_key(self, _widget, event):
        key = Gdk.keyval_name(event.keyval)
        ctrl_alt = (event.state & Gdk.ModifierType.CONTROL_MASK) and \
                   (event.state & Gdk.ModifierType.MOD1_MASK)
        if key in ("Return", "KP_Enter") and not self.session_active:
            # Enter means "connect" only while the connection list has focus.
            # Swallowing it unconditionally would make every toolbar button
            # unreachable from the keyboard, which matters on a client that
            # may not have a mouse at all.
            focus = self.get_focus()
            on_list = focus is None or isinstance(
                focus, (Gtk.ListBox, Gtk.ListBoxRow)
            ) or (focus.get_ancestor(Gtk.ListBox) is not None)
            if on_list:
                self.start_session()
                return True
            return False
        if key == "F5":
            self.cfg = tcconfig.load()
            self.refresh_list()
            self.set_status("Configuration reloaded.")
            return True
        if ctrl_alt and key in ("F12", "s", "S"):
            self.on_settings()
            return True
        return False


def main():
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    settings = Gtk.Settings.get_default()
    settings.set_property("gtk-application-prefer-dark-theme", True)

    window = ThinClient()
    window.show_all()

    # The NetworkManager dispatcher SIGHUPs us once a link is up and the
    # central configuration has been fetched.
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGHUP, window.reload_config)

    auto = window.cfg["device"].get("auto_connect", "")
    if auto:
        conn = tcconfig.find(window.cfg, auto)
        if conn:
            GLib.timeout_add(600, lambda: (window.start_session(conn), False)[1])

    Gtk.main()


if __name__ == "__main__":
    main()
