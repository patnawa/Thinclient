#!/bin/bash
# Render the connection manager on a headless X server and screenshot it.
# Catches GTK mistakes in seconds instead of after a 4-minute image build.
#   bash build/uitest.sh [output.png]
#   bash build/uitest.sh out.png settings    # screenshot the Settings dialog
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SHOT="${1:-/tmp/tc-ui.png}"
MODE="${2:-manager}"
DISP=:99

command -v Xvfb >/dev/null || { echo "install xvfb"; exit 1; }
command -v import >/dev/null || { echo "install imagemagick"; exit 1; }

# Stage the overlay exactly where the image puts it.
install -d /usr/local/lib/thinclient /etc/thinclient /run/thinclient
for f in "$REPO"/overlay/usr/local/lib/thinclient/*.py; do
  sed 's/\r$//' "$f" > "/usr/local/lib/thinclient/$(basename "$f")"
done
sed 's/\r$//' "$REPO/overlay/etc/thinclient/config.json" > /etc/thinclient/config.json
cat > /etc/thinclient/build-info <<'EOF'
name=ThinClient
version=1.0
base=Debian trixie
freerdp=3.15.0+dfsg-2.1
kernel=6.12.101-1
EOF

# A second connection, so the list is worth looking at.
python3 - <<'PYEOF'
import json, copy
c = json.load(open("/etc/thinclient/config.json"))
extra = copy.deepcopy(c["connections"][0])
extra.update({"id": "acct", "name": "Accounting (RemoteApp)", "host": "10.0.0.32",
              "domain": "CORP", "username": "svc-acct", "display": "multimon"})
c["connections"].append(extra)
json.dump(c, open("/etc/thinclient/config.json", "w"), indent=2)
PYEOF

pkill -f "Xvfb $DISP" 2>/dev/null
Xvfb "$DISP" -screen 0 1280x800x24 >/dev/null 2>&1 &
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

parent = Gtk.Window()
parent.show()
dialog = SettingsDialog(parent, tcconfig.load())
dialog.show_all()
Gtk.main()
PYEOF
  DISPLAY=$DISP python3 /tmp/tc-settings-driver.py > /tmp/tc-ui.log 2>&1 &
else
  DISPLAY=$DISP python3 /usr/local/lib/thinclient/manager.py > /tmp/tc-ui.log 2>&1 &
fi
APP=$!
sleep 8

if ! kill -0 "$APP" 2>/dev/null; then
  echo "MANAGER CRASHED:"; cat /tmp/tc-ui.log
  kill "$OB" "$XVFB" 2>/dev/null; exit 1
fi

DISPLAY=$DISP import -window root "$SHOT" 2>/dev/null
echo "screenshot: $SHOT"
DISPLAY=$DISP xwininfo -root -children 2>/dev/null | grep -c '0x' | \
  xargs -I{} echo "mapped windows: {}"
[ -s /tmp/tc-ui.log ] && { echo "--- stderr ---"; cat /tmp/tc-ui.log; }

kill "$APP" "$OB" "$XVFB" 2>/dev/null
exit 0
