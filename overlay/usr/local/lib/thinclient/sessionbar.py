#!/usr/bin/env python3
"""A pull-down bar that floats above a full-screen RDP session.

A full-screen session covers everything, so without this there is no visible
way back: the user has to know a FreeRDP key combination, or sign out on the
server. That is fine for staff who were told; it is a support call for everyone
else.

Collapsed it is a small tab at the top edge, a few pixels tall, so it does not
sit on top of the remote desktop. Moving the pointer onto it expands the bar.

    tc-sessionbar <pid> <connection name>

Disconnecting writes a marker before terminating FreeRDP, so the connection
manager can tell a deliberate disconnect from a dropped session and does not
try to reconnect.
"""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

import os  # noqa: E402
import signal  # noqa: E402
import sys  # noqa: E402

DISCONNECT_MARKER = "/run/thinclient/disconnect-requested"

TAB_WIDTH = 180
TAB_HEIGHT = 5
BAR_WIDTH = 460
BAR_HEIGHT = 46

CSS = b"""
window            { background-color: rgba(0,0,0,0); }
.bar              { background-color: #0f1215; border: 1px solid #2a323b;
                    border-top: none; border-radius: 0px 0px 8px 8px; }
.tab              { background-color: #2f6fd0; border-radius: 0px 0px 6px 6px; }
.name             { color: #d7dde4; font-size: 13px; padding: 0px 14px; }
button.disconnect { background-image: none; background-color: #a8322c;
                    color: #ffffff; border: none; border-radius: 5px;
                    padding: 6px 16px; font-size: 13px; font-weight: bold; }
button.disconnect:hover { background-color: #c43b34; }
button.plain      { background-image: none; background-color: #262d35;
                    color: #e6ebf0; border: 1px solid #333c46;
                    border-radius: 5px; padding: 6px 14px; font-size: 13px; }
"""


class SessionBar(Gtk.Window):
    def __init__(self, pid, name):
        # POPUP makes this an override-redirect window: the window manager never
        # sees it, so it cannot be reparented, decorated, resized or stacked
        # behind the full-screen session. A normal toplevel with a DOCK hint got
        # a frame from Openbox and ignored the size we asked for.
        super().__init__(type=Gtk.WindowType.POPUP)
        self.pid = pid
        self.expanded = False
        self._collapse_timer = None

        self.set_keep_above(True)
        self.set_accept_focus(False)
        self.set_resizable(False)

        # No compositor runs in this session, so an ARGB visual would render as
        # a black rectangle. Everything here is opaque by design.

        self.stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(self.stack)
        self.stack.show()

        # --- collapsed: a small tab ---------------------------------------
        self.tab = Gtk.Box()
        self.tab.get_style_context().add_class("tab")
        self.tab.set_size_request(TAB_WIDTH, TAB_HEIGHT)
        self.stack.pack_start(self.tab, False, False, 0)

        # --- expanded: the actual controls --------------------------------
        self.bar = Gtk.Box(spacing=10)
        self.bar.get_style_context().add_class("bar")
        self.bar.set_border_width(6)

        label = Gtk.Label(label=name, xalign=0)
        label.get_style_context().add_class("name")
        label.set_ellipsize(3)
        self.bar.pack_start(label, True, True, 0)

        windowed = Gtk.Button(label="Windowed")
        windowed.get_style_context().add_class("plain")
        windowed.set_tooltip_text("Leave full screen (Ctrl+Alt+Enter)")
        windowed.connect("clicked", self.on_windowed)
        self.bar.pack_start(windowed, False, False, 0)

        disconnect = Gtk.Button(label="Disconnect")
        disconnect.get_style_context().add_class("disconnect")
        disconnect.connect("clicked", self.on_disconnect)
        self.bar.pack_start(disconnect, False, False, 0)

        self.stack.pack_start(self.bar, False, False, 0)

        self.connect("enter-notify-event", self.on_enter)
        self.connect("leave-notify-event", self.on_leave)
        self.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)

        self.collapse()
        GLib.timeout_add_seconds(2, self.check_session)

    # ------------------------------------------------------------ layout --
    def geometry(self):
        display = Gdk.Display.get_default()
        try:
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            return monitor.get_geometry()
        except (AttributeError, TypeError):
            screen = self.get_screen()
            rect = Gdk.Rectangle()
            rect.x, rect.y = 0, 0
            rect.width, rect.height = screen.get_width(), screen.get_height()
            return rect

    def place(self, width, height):
        area = self.geometry()
        # set_size_request pins the minimum; without it GTK grows the window to
        # whatever its contents want and the 5px tab becomes a slab.
        self.set_size_request(width, height)
        self.resize(width, height)
        self.move(area.x + (area.width - width) // 2, area.y)

    def collapse(self):
        self.expanded = False
        self.bar.hide()
        self.tab.show_all()
        self.place(TAB_WIDTH, TAB_HEIGHT)

    def expand(self):
        self.expanded = True
        self.tab.hide()
        self.bar.show_all()
        self.place(BAR_WIDTH, BAR_HEIGHT)

    # ------------------------------------------------------------ events --
    def on_enter(self, *_):
        if self._collapse_timer:
            GLib.source_remove(self._collapse_timer)
            self._collapse_timer = None
        if not self.expanded:
            self.expand()
        return False

    def on_leave(self, _widget, event):
        # Ignore the crossing events generated while the window resizes.
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        if self._collapse_timer:
            GLib.source_remove(self._collapse_timer)
        self._collapse_timer = GLib.timeout_add(700, self._collapse_now)
        return False

    def _collapse_now(self):
        self._collapse_timer = None
        if self.expanded:
            self.collapse()
        return False

    # ----------------------------------------------------------- actions --
    def on_windowed(self, *_):
        # FreeRDP toggles full screen on Ctrl+Alt+Enter; ask X to deliver it.
        os.system("xdotool key --clearmodifiers ctrl+alt+Return 2>/dev/null") \
            if os.path.exists("/usr/bin/xdotool") else None
        self.collapse()

    def on_disconnect(self, *_):
        # Record that this was deliberate before killing the client, so the
        # connection manager does not treat it as a dropped session and start
        # counting down to a reconnect.
        try:
            os.makedirs("/run/thinclient", exist_ok=True)
            with open(DISCONNECT_MARKER, "w", encoding="utf-8") as fh:
                fh.write("%d\n" % self.pid)
        except OSError:
            pass
        try:
            os.kill(self.pid, signal.SIGTERM)
        except OSError:
            pass
        Gtk.main_quit()

    def check_session(self):
        """Disappear if the session ended by itself."""
        try:
            os.kill(self.pid, 0)
        except OSError:
            Gtk.main_quit()
            return False
        return True


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: tc-sessionbar <pid> [connection name]")
    try:
        pid = int(sys.argv[1])
    except ValueError:
        sys.exit("the first argument must be a process id")
    name = sys.argv[2] if len(sys.argv) > 2 else "Remote session"

    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    window = SessionBar(pid, name)
    window.show()
    window.collapse()
    Gtk.main()


if __name__ == "__main__":
    main()
