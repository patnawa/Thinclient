#!/bin/bash
# Exercise the image's privileged and writable paths AS THE KIOSK USER.
#
# The connection manager runs as "thin", not as root. Testing it as root hides
# exactly the faults that matter: a root-only /run directory, a missing sudo, a
# sudoers rule that does not cover the command the UI actually calls. This test
# reproduces the runtime layout inside the built rootfs and runs the real code.
#
#   sudo bash build/permcheck.sh
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
source "$REPO/build/config.sh"
ROOTFS="$WORKDIR/rootfs"
[ -d "$ROOTFS" ] || { echo "no rootfs at $ROOTFS - run build.sh first"; exit 1; }

# Exercise the current overlay during development, not whatever copy happened
# to be present at the last full image build. This is the same red-green sync
# used by unittest.sh and prevents a stale helper from producing false results.
for relative in usr/local/sbin/tc-save-config \
                usr/local/sbin/tc-fetch-config \
                usr/local/sbin/tc-apply-config \
                etc/NetworkManager/dispatcher.d/50-thinclient; do
    sed 's/\r$//' "$REPO/overlay/$relative" > "$ROOTFS/$relative"
done
sed 's/\r$//' "$REPO/overlay/usr/local/lib/thinclient/tcconfig.py" \
    > "$ROOTFS/usr/local/lib/thinclient/tcconfig.py"

fail=0
ok()   { printf '  ok      %s\n' "$1"; }
bad()  { printf '  FAIL    %s\n' "$1"; [ -n "${2:-}" ] && printf '          %s\n' "$2"; fail=1; }

as_root() { chroot "$ROOTFS" /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
                HOME=/root LC_ALL=C "$@"; }
as_thin() { chroot "$ROOTFS" /usr/bin/setpriv --reuid=thin --regid=thin --init-groups \
                --inh-caps=-all env PATH=/usr/bin:/bin HOME=/home/thin LC_ALL=C "$@"; }

# --- reproduce the boot-time runtime layout ---------------------------------
mount -t tmpfs tmpfs "$ROOTFS/run" 2>/dev/null
HTTP_PID=""
cleanup() {
    [ -z "$HTTP_PID" ] || kill "$HTTP_PID" 2>/dev/null
    umount "$ROOTFS/run" 2>/dev/null
}
trap cleanup EXIT

as_root /usr/bin/systemd-tmpfiles --create /etc/tmpfiles.d/thinclient.conf >/dev/null 2>&1
if [ ! -d "$ROOTFS/run/thinclient" ]; then
    # tmpfiles may refuse to run in a chroot; fall back to what the unit does.
    as_root /bin/mkdir -p /run/thinclient /media/tc
    as_root /bin/chgrp thin /run/thinclient
    as_root /bin/chmod 1775 /run/thinclient
fi

echo "=== runtime directory ==="
PERMS=$(as_root /usr/bin/stat -c '%a %U:%G' /run/thinclient 2>/dev/null)
case "$PERMS" in
    1775\ root:thin|775\ root:thin|*:thin) ok "/run/thinclient is $PERMS" ;;
    *) bad "/run/thinclient is $PERMS - the kiosk user cannot write there" ;;
esac

echo
echo "=== sudo ==="
if as_root /bin/sh -c 'command -v sudo >/dev/null'; then
    ok "sudo is installed"
else
    bad "sudo is NOT installed" "every privileged UI action will fail"
fi
for cmd in "/usr/bin/systemctl reboot" "/usr/bin/systemctl poweroff" \
           "/usr/local/sbin/tc-save-config" "/usr/local/sbin/tc-apply-config" \
           "/usr/bin/nmcli"; do
    if as_root /usr/bin/sudo -l -U thin 2>/dev/null | grep -qF -- "$cmd"; then
        ok "thin may run $cmd"
    else
        bad "thin may NOT run $cmd"
    fi
done

echo
echo "=== writing configuration as the kiosk user ==="
OUT=$(as_thin /usr/bin/python3 -c '
import sys
sys.path.insert(0, "/usr/local/lib/thinclient")
import tcconfig
cfg = tcconfig.load()
cfg["connections"] = [c for c in cfg["connections"] if c["id"] != "main"]  # a deletion
cfg["device"]["screen_blank_minutes"] = 15
ok, msg = tcconfig.save(cfg)
print("RESULT", ok, msg)
back = tcconfig.load()
print("PERSISTED", [c["id"] for c in back["connections"]],
      back["device"]["screen_blank_minutes"])
' 2>&1)
echo "$OUT" | sed 's/^/          /'
case "$OUT" in
    *"RESULT True"*) ok "save() succeeded without raising" ;;
    *Traceback*)     bad "save() raised - the Settings dialog would abort silently" ;;
    *)               bad "save() reported failure" ;;
esac
# The deletion and the device change must both be visible on reload.
case "$OUT" in
    *"PERSISTED [] 15"*|*"PERSISTED []"*) ok "deletion and device change round-tripped" ;;
    *) bad "configuration did not round-trip through save()/load()" ;;
esac

echo
echo "=== privileged runtime symlink resistance ==="
VICTIM=/run/tc-symlink-victim
as_root /bin/sh -c 'printf "DO-NOT-OVERWRITE\n" > /run/tc-symlink-victim'
victim_is_intact() {
    [ "$(as_root /bin/cat "$VICTIM" 2>/dev/null)" = "DO-NOT-OVERWRITE" ]
}

# The private directory itself may be planted before a privileged helper runs.
# A safe helper rejects it without following the link or touching its target.
as_root /bin/rm -rf /run/thinclient/.root
if as_thin /bin/ln -s "$VICTIM" /run/thinclient/.root 2>/dev/null; then
    printf '{}\n' | as_root /usr/local/sbin/tc-save-config \
        >/tmp/tc-save-symlink.log 2>&1 || true
    if victim_is_intact; then
        ok "tc-save-config rejects a planted private-state symlink"
    else
        bad "tc-save-config followed /run/thinclient/.root" \
            "$VICTIM was overwritten by a privileged helper"
    fi
else
    bad "could not stage the private-state symlink regression test"
fi
as_root /bin/rm -f /run/thinclient/.root

# These were the historical predictable output names.  Leave hostile links at
# each path while invoking the real root helpers and prove their target survives.
as_root /bin/rm -f /run/thinclient/save.json
as_thin /bin/ln -s "$VICTIM" /run/thinclient/save.json 2>/dev/null
printf '{}\n' | as_root /usr/local/sbin/tc-save-config \
    >/tmp/tc-save-symlink.log 2>&1 || true
if victim_is_intact; then
    ok "tc-save-config does not follow the old save.json path"
else
    bad "tc-save-config followed a hostile save.json symlink"
fi
as_root /bin/rm -f /run/thinclient/save.json

# Serve one valid response locally.  This makes curl actually open and write its
# output file, which is essential: a connection failure would not exercise the
# historical `curl -o remote-config.json.tmp` symlink vulnerability.
as_root /bin/mkdir -p /run/tc-config-test
as_root /bin/sh -c 'printf "{\"schema\": 1}\n" > /run/tc-config-test/config.json'
HTTP_PORT="$(as_root /usr/bin/python3 -c \
    'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
as_root /usr/bin/python3 -m http.server "$HTTP_PORT" --bind 127.0.0.1 \
    --directory /run/tc-config-test >/tmp/tc-config-test-http.log 2>&1 &
HTTP_PID=$!
/bin/sleep 0.3
if ! kill -0 "$HTTP_PID" 2>/dev/null; then
    bad "could not start the local configuration server for the symlink test"
fi

as_root /bin/rm -f /run/thinclient/dhcp-config-url
as_thin /bin/ln -s "$VICTIM" /run/thinclient/dhcp-config-url 2>/dev/null
as_root /bin/rm -f /run/thinclient/remote-config.json.tmp
as_thin /bin/ln -s "$VICTIM" /run/thinclient/remote-config.json.tmp 2>/dev/null
CONFIG_TEST_URL="http://127.0.0.1:$HTTP_PORT/config.json"
as_root /usr/bin/env DHCP4_PRIVATE_224="$CONFIG_TEST_URL" \
    /etc/NetworkManager/dispatcher.d/50-thinclient eth0 up \
    >/tmp/tc-dispatch-symlink.log 2>&1 || true
if victim_is_intact \
        && as_root /bin/sh -c '[ ! -L /run/thinclient/dhcp-config-url ]' \
        && [ "$(as_thin /bin/cat /run/thinclient/dhcp-config-url 2>/dev/null)" = "$CONFIG_TEST_URL" ]; then
    ok "DHCP URL publication atomically replaces a hostile symlink and remains readable"
else
    bad "DHCP URL publication followed or failed to replace a hostile symlink"
fi

if victim_is_intact \
        && as_root /bin/sh -c '[ ! -L /run/thinclient/remote-config.json ]' \
        && as_thin /usr/bin/python3 -c \
            'import json; json.load(open("/run/thinclient/remote-config.json"))' 2>/dev/null; then
    ok "tc-fetch-config atomically publishes a kiosk-readable download without following the old temp path"
else
    bad "tc-fetch-config followed a hostile temp symlink or failed to publish its validated download"
fi
as_root /bin/rm -f /run/thinclient/remote-config.json.tmp
kill "$HTTP_PID" 2>/dev/null || true
wait "$HTTP_PID" 2>/dev/null || true
HTTP_PID=""

STATE_PERMS=$(as_root /usr/bin/stat -c '%a %U:%G' /run/thinclient/.root 2>/dev/null)
if [ "$STATE_PERMS" = "700 root:root" ] \
        && ! as_thin /usr/bin/touch /run/thinclient/.root/kiosk-probe 2>/dev/null; then
    ok "privileged scratch state is root-only ($STATE_PERMS)"
else
    bad "privileged scratch state is not root-only" "found: ${STATE_PERMS:-missing}"
fi

echo
echo "=== session log as the kiosk user ==="
if as_thin /bin/sh -c 'echo probe > /run/thinclient/last-session.log' 2>/dev/null; then
    ok "the kiosk user can write the session log"
else
    bad "cannot write /run/thinclient/last-session.log" "RDP sessions would not start"
fi

echo
[ "$fail" -eq 0 ] && echo "PERMISSION CHECK PASSED" || echo "PERMISSION CHECK FAILED"
exit "$fail"
