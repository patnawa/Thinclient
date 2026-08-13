#!/bin/bash
# End-to-end proof that the image's RDP client can establish a real session:
# stand up an RDP server on the build host, connect to it with the FreeRDP
# binary from inside the built image, and screenshot the result.
#
#   sudo bash build/rdpsession-test.sh
#
# Needs on the BUILD HOST (not in the image): freerdp3-shadow-x11, xvfb,
# imagemagick, x11-apps, openbox.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
source "$REPO/build/config.sh"
ROOTFS="$WORKDIR/rootfs"
SHOT="${1:-$REPO/out/rdp-session.png}"
SERVER_DISPLAY=:98      # what the RDP server publishes
CLIENT_DISPLAY=:97      # where the RDP client draws
PORT=13389

[ -d "$ROOTFS" ] || { echo "no rootfs at $ROOTFS - run build.sh first"; exit 1; }
command -v freerdp-shadow-cli3 >/dev/null || command -v freerdp-shadow-cli >/dev/null || {
    echo "install freerdp3-shadow-x11 on the build host"; exit 1; }
SHADOW=$(command -v freerdp-shadow-cli3 || command -v freerdp-shadow-cli)

cleanup() {
    pkill -f "freerdp-shadow" 2>/dev/null
    pkill -f "Xvfb $SERVER_DISPLAY" 2>/dev/null
    pkill -f "Xvfb $CLIENT_DISPLAY" 2>/dev/null
    umount "$ROOTFS/tmp/.X11-unix" 2>/dev/null
}
trap cleanup EXIT
cleanup; sleep 1

export GDK_BACKEND=x11
unset WAYLAND_DISPLAY

# --- the "server" desktop ----------------------------------------------------
Xvfb "$SERVER_DISPLAY" -screen 0 1024x768x24 >/dev/null 2>&1 &
sleep 2
DISPLAY=$SERVER_DISPLAY openbox >/dev/null 2>&1 &
DISPLAY=$SERVER_DISPLAY xsetroot -solid "#204070" 2>/dev/null
# Something unmistakable to look for in the client's screenshot.
DISPLAY=$SERVER_DISPLAY xmessage -center -geometry 600x200 \
    "REMOTE DESKTOP OK" >/dev/null 2>&1 &
sleep 1

echo "starting RDP server on port $PORT"
# The X11 shadow subsystem takes its display from the environment. -auth drops
# the SAM requirement; -sec-nla lets the client negotiate plain TLS.
DISPLAY=$SERVER_DISPLAY $SHADOW /port:$PORT -auth -sec-nla \
    > /tmp/tc-shadow.log 2>&1 &
sleep 4
if ! pgrep -f freerdp-shadow >/dev/null; then
    echo "shadow server failed to start:"; tail -15 /tmp/tc-shadow.log; exit 1
fi

# --- the client, taken from inside the image --------------------------------
Xvfb "$CLIENT_DISPLAY" -screen 0 1280x800x24 >/dev/null 2>&1 &
sleep 2
DISPLAY=$CLIENT_DISPLAY openbox >/dev/null 2>&1 &
sleep 1

# The chroot needs the X socket directory to reach our Xvfb.
mkdir -p "$ROOTFS/tmp/.X11-unix"
mount --bind /tmp/.X11-unix "$ROOTFS/tmp/.X11-unix"

echo "connecting with the image's FreeRDP"
chroot "$ROOTFS" /usr/bin/env -i PATH=/usr/bin:/bin HOME=/root LC_ALL=C \
    DISPLAY="$CLIENT_DISPLAY" \
    /usr/bin/xfreerdp3 /v:127.0.0.1:$PORT /u:probe /p:probe /cert:ignore \
        /size:1024x768 /gfx /network:lan +clipboard \
        /drive:USB,/media/tc /timeout:20000 /log-level:INFO \
    > /tmp/tc-rdpclient.log 2>&1 &
CLIENT=$!
sleep 12

if ! kill -0 "$CLIENT" 2>/dev/null; then
    echo "RDP CLIENT EXITED EARLY:"
    tail -20 /tmp/tc-rdpclient.log
    exit 1
fi

DISPLAY=$CLIENT_DISPLAY import -window root "$SHOT" 2>/dev/null
echo "screenshot: $SHOT"
echo "--- client log (tail) ---"
tail -12 /tmp/tc-rdpclient.log
kill "$CLIENT" 2>/dev/null
exit 0
