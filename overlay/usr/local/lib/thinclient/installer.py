#!/usr/bin/env python3
"""Graphical front end for installing ThinClient onto a local disk.

Launched instead of the connection manager when the client is booted with
tc.install=1. Deliberately plain: pick a disk, read the warning, confirm by
typing, watch it happen, reboot.
"""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

import subprocess  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402

sys.path.insert(0, "/usr/local/lib/thinclient")
from manager import CSS  # noqa: E402  - one visual language for the whole product

INSTALLER = "/usr/local/sbin/tc-install"


class Installer(Gtk.Window):
    def __init__(self):
        super().__init__(title="Install ThinClient")
        self.get_style_context().add_class("tc-root")
        self.set_default_size(900, 640)
        self.connect("destroy", Gtk.main_quit)
        self.connect("delete-event", self.on_delete)
        self.fullscreen()
        self.running = False
        self.idle_controls = []

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(outer)

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header.get_style_context().add_class("tc-header")
        title = Gtk.Label(label="Install ThinClient", xalign=0)
        title.get_style_context().add_class("tc-title")
        header.pack_start(title, False, False, 0)
        subtitle = Gtk.Label(
            label="The selected disk will be erased completely.", xalign=0)
        subtitle.get_style_context().add_class("tc-sub")
        header.pack_start(subtitle, False, False, 0)
        outer.pack_start(header, False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                       margin_start=22, margin_end=22, margin_top=14)
        outer.pack_start(body, True, True, 0)

        label = Gtk.Label(label="INSTALL ONTO", xalign=0)
        label.get_style_context().add_class("tc-listlabel")
        body.pack_start(label, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.disks = Gtk.ListBox()
        self.disks.get_style_context().add_class("tc-list")
        self.disks.set_selection_mode(Gtk.SelectionMode.SINGLE)
        scroller.add(self.disks)
        body.pack_start(scroller, True, True, 0)

        self.progress = Gtk.ProgressBar(show_text=True)
        body.pack_start(self.progress, False, False, 0)

        self.status = Gtk.Label(label="", xalign=0)
        self.status.get_style_context().add_class("tc-status")
        outer.pack_start(self.status, False, False, 0)

        bar = Gtk.Box(spacing=10)
        bar.get_style_context().add_class("tc-bar")
        self.install_btn = Gtk.Button(label="Install")
        for cls in ("tc-btn", "tc-primary"):
            self.install_btn.get_style_context().add_class(cls)
        self.install_btn.connect("clicked", self.on_install)
        bar.pack_start(self.install_btn, False, False, 0)

        refresh = Gtk.Button(label="Rescan")
        refresh.get_style_context().add_class("tc-btn")
        refresh.connect("clicked", lambda *_: self.load_disks())
        bar.pack_start(refresh, False, False, 0)
        self.idle_controls.append(refresh)

        for text, action in (("Restart", "reboot"), ("Shut Down", "poweroff")):
            button = Gtk.Button(label=text)
            for cls in ("tc-btn", "tc-danger"):
                button.get_style_context().add_class(cls)
            button.connect("clicked", lambda _b, a=action: self.power(a))
            bar.pack_end(button, False, False, 0)
            self.idle_controls.append(button)
        outer.pack_start(bar, False, False, 0)

        self.load_disks()

    # ------------------------------------------------------------ disks ----
    def load_disks(self):
        for child in self.disks.get_children():
            self.disks.remove(child)

        try:
            import json
            output = subprocess.run([INSTALLER, "--list"], capture_output=True,
                                    text=True, timeout=30)
            found = json.loads(output.stdout) if output.returncode == 0 else []
        except (OSError, subprocess.SubprocessError, ValueError):
            found = []

        if not found:
            row = Gtk.ListBoxRow(activatable=False, selectable=False)
            message = Gtk.Label(
                label="No suitable disk was found. The disk this client booted "
                      "from is never offered as a target.", xalign=0)
            message.get_style_context().add_class("tc-empty")
            message.set_line_wrap(True)
            row.add(message)
            self.disks.add(row)
            self.install_btn.set_sensitive(False)
        else:
            for disk in found:
                row = Gtk.ListBoxRow()
                row.disk = disk
                row.set_sensitive(not disk["too_small"])
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                name = Gtk.Label(label="%s   %.1f GB" % (disk["path"], disk["size_gb"]),
                                 xalign=0)
                name.get_style_context().add_class("tc-conn-name")
                box.pack_start(name, False, False, 0)
                detail = "%s%s%s" % (
                    disk["model"],
                    "   removable" if disk["removable"] else "",
                    "   TOO SMALL" if disk["too_small"] else "")
                sub = Gtk.Label(label=detail, xalign=0)
                sub.get_style_context().add_class("tc-conn-host")
                box.pack_start(sub, False, False, 0)
                row.add(box)
                self.disks.add(row)
            self.install_btn.set_sensitive(True)

        self.disks.show_all()
        first = self.disks.get_row_at_index(0)
        if first is not None and getattr(first, "disk", None):
            self.disks.select_row(first)

    # ---------------------------------------------------------- install ----
    def on_install(self, *_):
        if self.running:
            return
        row = self.disks.get_selected_row()
        disk = getattr(row, "disk", None) if row else None
        if not disk:
            self.status.set_text("Select a disk first.")
            return

        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text="Erase %s and install ThinClient?" % disk["path"],
        )
        dialog.format_secondary_text(
            "%s, %.1f GB.\n\nEverything on this disk will be destroyed, including "
            "any existing operating system. Type INSTALL to confirm."
            % (disk["model"], disk["size_gb"]))
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        proceed = dialog.add_button("Erase and install", Gtk.ResponseType.OK)
        proceed.get_style_context().add_class("destructive-action")
        proceed.set_sensitive(False)

        entry = Gtk.Entry(margin=12)
        entry.set_placeholder_text("type INSTALL")
        entry.connect("changed",
                      lambda e: proceed.set_sensitive(e.get_text().strip() == "INSTALL"))
        dialog.get_content_area().add(entry)
        dialog.show_all()
        confirmed = dialog.run() == Gtk.ResponseType.OK
        dialog.destroy()
        if not confirmed:
            return

        self.running = True
        self.install_btn.set_sensitive(False)
        self.disks.set_sensitive(False)
        for control in self.idle_controls:
            control.set_sensitive(False)
        self.progress.set_fraction(0.05)
        self.progress.set_text("starting")
        self.status.set_text("Installing to %s - do not switch the machine off."
                             % disk["path"])
        threading.Thread(target=self._worker, args=(disk["path"],), daemon=True).start()
        GLib.timeout_add(500, self._pulse)

    def _pulse(self):
        if not self.running:
            return False
        self.progress.pulse()
        return True

    def _worker(self, path):
        try:
            # The UI runs as the unprivileged kiosk user; partitioning does not.
            result = subprocess.run(
                ["sudo", "-n", INSTALLER, "--target", path, "--yes"],
                capture_output=True, text=True, timeout=1800,
            )
            ok = result.returncode == 0
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            message = detail[-1] if detail else ""
        except (OSError, subprocess.SubprocessError) as exc:
            ok, message = False, str(exc)
        GLib.idle_add(self._done, ok, message)

    def _done(self, ok, message):
        self.running = False
        self.progress.set_fraction(1.0 if ok else 0.0)
        self.progress.set_text("finished" if ok else "failed")
        self.disks.set_sensitive(True)
        self.install_btn.set_sensitive(True)
        for control in self.idle_controls:
            control.set_sensitive(True)

        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.INFO if ok else Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.NONE,
            text="Installation complete" if ok else "Installation failed",
        )
        dialog.format_secondary_text(
            "Remove the USB stick and restart. The client will boot from its "
            "internal disk." if ok else message)
        if ok:
            dialog.add_button("Restart now", Gtk.ResponseType.OK)
            dialog.add_button("Stay here", Gtk.ResponseType.CANCEL)
        else:
            dialog.add_button("Close", Gtk.ResponseType.CANCEL)
        response = dialog.run()
        dialog.destroy()
        if ok and response == Gtk.ResponseType.OK:
            self.power("reboot")
        return False

    def power(self, action):
        if self.running:
            self.status.set_text(
                "Installation is still writing the disk; wait for it to finish."
            )
            return
        subprocess.Popen(["sudo", "-n", "/usr/bin/systemctl", action])

    def on_delete(self, *_):
        """Do not let a window-manager shortcut interrupt a disk install."""
        if self.running:
            self.status.set_text(
                "Installation is still writing the disk; wait for it to finish."
            )
            return True
        return False


def main():
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
    window = Installer()
    window.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
