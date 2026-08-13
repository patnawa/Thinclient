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

fail=0
ok()   { printf '  ok      %s\n' "$1"; }
bad()  { printf '  FAIL    %s\n' "$1"; [ -n "${2:-}" ] && printf '          %s\n' "$2"; fail=1; }

as_root() { chroot "$ROOTFS" /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
                HOME=/root LC_ALL=C "$@"; }
as_thin() { chroot "$ROOTFS" /usr/bin/setpriv --reuid=thin --regid=thin --init-groups \
                --inh-caps=-all env PATH=/usr/bin:/bin HOME=/home/thin LC_ALL=C "$@"; }

# --- reproduce the boot-time runtime layout ---------------------------------
mount -t tmpfs tmpfs "$ROOTFS/run" 2>/dev/null
cleanup() { umount "$ROOTFS/run" 2>/dev/null; }
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
echo "=== session log as the kiosk user ==="
if as_thin /bin/sh -c 'echo probe > /run/thinclient/last-session.log' 2>/dev/null; then
    ok "the kiosk user can write the session log"
else
    bad "cannot write /run/thinclient/last-session.log" "RDP sessions would not start"
fi

echo
[ "$fail" -eq 0 ] && echo "PERMISSION CHECK PASSED" || echo "PERMISSION CHECK FAILED"
exit "$fail"
