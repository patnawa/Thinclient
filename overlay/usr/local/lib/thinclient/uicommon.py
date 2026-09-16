"""Shared GTK theme and small widgets, independent of the main window."""
import socket
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango

CSS = b"""
window, .tc-root            { background-color: #16191d; }
.tc-header                  { background-color: #0f1215; padding: 16px 24px; }
.tc-title                   { color: #ffffff; font-size: 22px; font-weight: bold; }
.tc-sub                     { color: #a5b0bd; font-size: 14px; }
.tc-clock                   { color: #d7dde4; font-size: 17px; font-weight: bold; }
.tc-net-good                { color: #79d69a; font-size: 14px; font-weight: bold; }
.tc-net-bad                 { color: #ff9a92; font-size: 14px; font-weight: bold; }
.tc-body-title              { color: #ffffff; font-size: 20px; font-weight: bold; }
.tc-listlabel               { color: #a5b0bd; font-size: 13px; font-weight: bold;
                              letter-spacing: 1px; margin-top: 8px; }
list.tc-list                { background-color: transparent; }
list.tc-list row            { background-color: #1f242a; border-radius: 8px;
                              margin: 5px 0px; padding: 15px 18px; min-height: 46px; }
list.tc-list row:selected   { background-color: #2f6fd0; }
list.tc-list row.tc-group-row { background-color: transparent; padding: 4px 2px 0px 2px;
                              margin: 0px; min-height: 22px; }
.tc-conn-name               { color: #ffffff; font-size: 17px; font-weight: bold; }
.tc-conn-desc               { color: #b3bdc8; font-size: 15px; }
.tc-conn-badge              { color: #dbe8fb; font-size: 13px; font-weight: bold; }
.tc-conn-ready              { color: #8ce3aa; font-size: 13px; }
.tc-conn-offline            { color: #ffb0aa; font-size: 13px; }
list.tc-list row:selected .tc-conn-desc,
list.tc-list row:selected .tc-conn-ready,
list.tc-list row:selected .tc-conn-offline { color: #ffffff; }
.tc-status-box              { background-color: #202832; border-top: 1px solid #303a45;
                              padding: 11px 24px; }
.tc-status-box-bad          { background-color: #452522; border-top: 1px solid #713a35;
                              padding: 11px 24px; }
.tc-status                  { color: #e7edf4; font-size: 15px; font-weight: bold; }
.tc-status-bad              { color: #ffd2ce; font-size: 15px; font-weight: bold; }
.tc-auto                    { background-color: #243852; padding: 10px 16px;
                              border-radius: 7px; }
.tc-auto-label              { color: #ffffff; font-size: 15px; font-weight: bold; }
.tc-bar                     { background-color: #0f1215; padding: 12px 24px; }
button.tc-btn               { background-image: none; background-color: #262d35;
                              color: #e6ebf0; border: 1px solid #333c46;
                              border-radius: 6px; padding: 11px 20px; font-size: 16px;
                              min-height: 22px; }
button.tc-btn:hover         { background-color: #313a44; }
button.tc-btn:focus, list.tc-list row:focus {
                              box-shadow: inset 0 0 0 2px #f4f8ff; }
button.tc-primary           { background-color: #2f6fd0; color: #ffffff;
                              border-color: #2f6fd0; font-weight: bold; min-width: 210px; }
button.tc-primary:hover     { background-color: #3d80e6; }
button.tc-danger:hover      { background-color: #a8322c; border-color: #a8322c; color: #fff; }
.tc-empty                   { color: #b3bdc8; font-size: 16px; padding: 18px; }
.tc-about-title             { color: #ffffff; font-size: 24px; font-weight: bold; }
.tc-about-section           { color: #78a9ef; font-size: 11px; font-weight: bold;
                              letter-spacing: 1px; margin-top: 8px; }
.tc-about-key               { color: #a5b0bd; font-size: 14px; }
.tc-about-value             { color: #e6ebf0; font-size: 14px; }
.tc-progress-title          { color: #ffffff; font-size: 20px; font-weight: bold; }
.tc-progress-stage          { color: #d9e2ec; font-size: 16px; }
.tc-error-detail            { color: #ffd2ce; font-size: 15px; }
"""

def primary_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))          # TEST-NET-1: routes, never answers
        return sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()

def product_title(info):
    """Return the product name and release without hard-coding either one."""
    name = str(info.get("name") or "ThinClient").strip() or "ThinClient"
    version = str(info.get("version") or "").strip()
    return "%s %s" % (name, version) if version else name

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
