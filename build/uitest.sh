#!/bin/bash
# Render the connection manager on a headless X server and screenshot it.
# Catches GTK mistakes in seconds instead of after a 4-minute image build.
#   bash build/uitest.sh [output.png]
#   bash build/uitest.sh out.png settings    # screenshot the Settings dialog
#   bash build/uitest.sh out.png about       # screenshot public Help/support
#   bash build/uitest.sh out.png changelog   # screenshot offline release notes
#   bash build/uitest.sh out.png network-test # screenshot on-demand preflight
#   TC_UI_SCREEN=1024x768 bash build/uitest.sh out.png manager
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
source "$REPO/build/config.sh"
SHOT="${1:-/tmp/tc-ui.png}"
MODE="${2:-manager}"
DISP=:99
SCREEN="${TC_UI_SCREEN:-1280x800}"
mkdir -p "$(dirname "$SHOT")"

command -v Xvfb >/dev/null || { echo "install xvfb"; exit 1; }
command -v import >/dev/null || { echo "install imagemagick"; exit 1; }

# Stage the overlay exactly where the image puts it.
install -d /usr/local/lib/thinclient /etc/thinclient /run/thinclient
for f in "$REPO"/overlay/usr/local/lib/thinclient/*.py; do
  sed 's/\r$//' "$f" > "/usr/local/lib/thinclient/$(basename "$f")"
done
sed 's/\r$//' "$REPO/overlay/etc/thinclient/config.json" > /etc/thinclient/config.json
cat > /etc/thinclient/build-info <<EOF
name=$DISTRO_NAME
version=$DISTRO_VERSION
profile=lite
base=Debian $SUITE
freerdp=3.15.0+dfsg-2.1
kernel=6.12.101-1
EOF

# A second connection, so the list is worth looking at.
python3 - <<'PYEOF'
import json, copy
c = json.load(open("/etc/thinclient/config.json"))
extra = copy.deepcopy(c["connections"][0])
extra.update({"id": "acct", "name": "Accounting", "host": "10.0.0.32",
              "description": "Published finance application", "group": "Applications",
              "app": "||Accounting", "domain": "CORP", "username": "svc-acct",
              "display": "multimon"})
c["connections"].append(extra)
json.dump(c, open("/etc/thinclient/config.json", "w"), indent=2)
PYEOF

pkill -f "Xvfb $DISP" 2>/dev/null
Xvfb "$DISP" -screen 0 "${SCREEN}x24" >/dev/null 2>&1 &
XVFB=$!
sleep 2

# The image runs Openbox; without a window manager, fullscreen() is ignored and
# the window never maps, so the test has to mirror the real session.
DISPLAY=$DISP openbox >/dev/null 2>&1 &
OB=$!
sleep 1

# On WSL, WSLg exports WAYLAND_DISPLAY and GTK would quietly connect to the
# Windows-side compositor instead of our Xvfb. tc-session pins the same thing.
export GDK_BACKEND=x11
export TC_CHANGELOG_FILE="$REPO/CHANGELOG.md"
unset WAYLAND_DISPLAY

if [ "$MODE" = "sessionbar" ]; then
  # Stand in for a running session: a long-lived process to attach to, and a
  # coloured backdrop so the bar can be seen sitting on top of it.
  DISPLAY=$DISP xsetroot -solid "#204070" 2>/dev/null
  sleep 300 &
  FAKE_SESSION=$!
  cat > /tmp/tc-bar-driver.py <<PYEOF
import sys
sys.path.insert(0, "/usr/local/lib/thinclient")
sys.argv = ["sessionbar", "$FAKE_SESSION", "Windows Server 2025"]
import sessionbar
# Show it expanded: a 5px tab is correct in use but invisible in a screenshot.
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
provider = Gtk.CssProvider(); provider.load_from_data(sessionbar.CSS)
Gtk.StyleContext.add_provider_for_screen(
    Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
w = sessionbar.SessionBar($FAKE_SESSION, "Windows Server 2025")
w.show()
GLib.timeout_add(500, lambda: (w.expand(), False)[1])
Gtk.main()
PYEOF
  DISPLAY=$DISP python3 /tmp/tc-bar-driver.py > /tmp/tc-ui.log 2>&1 &
elif [ "$MODE" = "credentials" ]; then
  cat > /tmp/tc-cred-driver.py <<'PYEOF'
import sys
sys.path.insert(0, "/usr/local/lib/thinclient")
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk
import tcconfig
from manager import CredentialDialog, CSS

provider = Gtk.CssProvider()
provider.load_from_data(CSS)
Gtk.StyleContext.add_provider_for_screen(
    Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)

parent = Gtk.Window()
parent.show()
dialog = CredentialDialog(parent, tcconfig.load()["connections"][0])
dialog.show_all()
# Report whether Connect is correctly refused while the fields are empty.
print("connect_sensitive_when_empty=%s" % dialog.connect_button.get_sensitive())
Gtk.main()
PYEOF
  DISPLAY=$DISP python3 /tmp/tc-cred-driver.py > /tmp/tc-ui.log 2>&1 &
elif [ "$MODE" = "settings" ]; then
  cat > /tmp/tc-settings-driver.py <<'PYEOF'
import sys
sys.path.insert(0, "/usr/local/lib/thinclient")
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import tcconfig
from settings import SettingsDialog

Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
parent = Gtk.Window()
parent.show()
dialog = SettingsDialog(parent, tcconfig.load())
dialog.show_all()
Gtk.main()
PYEOF
  DISPLAY=$DISP python3 /tmp/tc-settings-driver.py > /tmp/tc-ui.log 2>&1 &
elif [ "$MODE" = "about" ]; then
  cat > /tmp/tc-about-driver.py <<'PYEOF'
import sys
sys.path.insert(0, "/usr/local/lib/thinclient")
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import manager

provider = Gtk.CssProvider()
provider.load_from_data(manager.CSS)
Gtk.StyleContext.add_provider_for_screen(
    Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)

# Stable representative values make the documentation screenshot useful and
# avoid publishing the build workstation's hostname, address, or hardware.
manager.socket.gethostname = lambda: "TC-DEMO-01"
manager.primary_ip = lambda: "192.168.10.42"
manager.active_link_summary = lambda: "1 Gb/s"
manager.hardware_info = lambda: {
    "Architecture": "x86_64",
    "Processor": "Intel(R) Core(TM) i5-8250U · 8 logical CPUs",
    "Memory": "7.8 GiB",
    "Graphics": "Intel Corporation UHD Graphics 620",
    "Network": ("enp2s0 · Ethernet · e1000e · connected · 1 Gb/s · "
                "192.168.10.42/24\n"
                "wlp3s0 · Wi-Fi · iwlwifi · down"),
}

window = manager.ThinClient()
window.show_all()
GLib.timeout_add(500, lambda: (window.on_about(), False)[1])
Gtk.main()
PYEOF
  DISPLAY=$DISP python3 /tmp/tc-about-driver.py > /tmp/tc-ui.log 2>&1 &
elif [ "$MODE" = "admin" ] || [ "$MODE" = "progress" ] || \
     [ "$MODE" = "error" ] || [ "$MODE" = "changelog" ]; then
  cat > /tmp/tc-dialog-driver.py <<PYEOF
import sys
sys.path.insert(0, "/usr/local/lib/thinclient")
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk
import manager

provider = Gtk.CssProvider(); provider.load_from_data(manager.CSS)
Gtk.StyleContext.add_provider_for_screen(
    Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
parent = Gtk.Window(); parent.show()
connection = {"id": "main", "name": "Windows Server 2025"}
mode = "$MODE"
if mode == "admin":
    dialog = manager.AdminDialog(parent)
elif mode == "progress":
    dialog = manager.ConnectionProgressDialog(parent, connection, lambda: None)
    dialog.set_stage("Contacting server", "Checking Windows Server 2025 on port 3389…")
elif mode == "changelog":
    dialog = manager.ChangelogDialog(parent)
else:
    dialog = manager.ConnectionErrorDialog(
        parent, connection,
        "The server did not respond on port 3389. Check the server, firewall, and network route.")
dialog.show_all()
Gtk.main()
PYEOF
  DISPLAY=$DISP python3 /tmp/tc-dialog-driver.py > /tmp/tc-ui.log 2>&1 &
elif [ "$MODE" = "network-test" ]; then
  cat > /tmp/tc-network-test-driver.py <<'PYEOF'
import sys
sys.path.insert(0, "/usr/local/lib/thinclient")
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
import tcconfig
import networkdiag
from settings import NetworkDialog

Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
def demo_refresh(dialog, *_args):
    dialog.wired_devices = ["enp2s0"]
    dialog.status.set_markup(
        "<tt>enp2s0     ethernet  connected    192.168.10.42/24</tt>"
    )
NetworkDialog.refresh = demo_refresh
networkdiag.run_preflight = lambda target: (
    "Network diagnostics\n"
    "Target: Windows Server 2025 — 192.168.1.10:3389\n"
    "Local: enp2s0 — 192.168.10.42/24\n"
    "Default gateway: 192.168.10.1\n"
    "Gateway ping (informational): OK — reachable\n"
    "DNS: OK — not needed (IP address)\n"
    "Route to target: OK — enp2s0 from 192.168.10.42 via 192.168.10.1\n"
    "TCP 3389: OK — connected\n"
    "RDP: OK — HYBRID — TLS with CredSSP/NLA\n"
    "No credentials were sent."
)
parent = Gtk.Window(); parent.show()
dialog = NetworkDialog(parent, tcconfig.load()["connections"])
dialog.show_all()
notebook = next(child for child in dialog.get_content_area().get_children()[0].get_children()
                if isinstance(child, Gtk.Notebook))
notebook.set_current_page(2)
GLib.timeout_add(300, lambda: (dialog._run_network_test(), False)[1])
Gtk.main()
PYEOF
  DISPLAY=$DISP python3 /tmp/tc-network-test-driver.py > /tmp/tc-ui.log 2>&1 &
else
  cat > /tmp/tc-manager-driver.py <<'PYEOF'
import sys
sys.path.insert(0, "/usr/local/lib/thinclient")
import manager
manager.socket.gethostname = lambda: "TC-DEMO-01"
manager.primary_ip = lambda: "192.168.10.42"
manager.active_link_summary = lambda: "1 Gb/s"
manager.main()
PYEOF
  DISPLAY=$DISP python3 /tmp/tc-manager-driver.py > /tmp/tc-ui.log 2>&1 &
fi
APP=$!
sleep 8

if ! kill -0 "$APP" 2>/dev/null; then
  echo "MANAGER CRASHED:"; cat /tmp/tc-ui.log
  kill "$OB" "$XVFB" 2>/dev/null; exit 1
fi

# Keep hover tooltips out of deterministic screenshots when xdotool is present.
command -v xdotool >/dev/null && DISPLAY=$DISP xdotool mousemove 2 2 2>/dev/null
sleep 1
DISPLAY=$DISP import -window root "$SHOT" 2>/dev/null
[ -s "$SHOT" ] || {
  echo "SCREENSHOT FAILED: $SHOT was not created"
  kill "$APP" "$OB" "$XVFB" 2>/dev/null
  exit 1
}
echo "screenshot: $SHOT"
DISPLAY=$DISP xwininfo -root -children 2>/dev/null | grep -c '0x' | \
  xargs -I{} echo "mapped windows: {}"
[ -s /tmp/tc-ui.log ] && { echo "--- stderr ---"; cat /tmp/tc-ui.log; }

kill "$APP" "$OB" "$XVFB" 2>/dev/null
exit 0
