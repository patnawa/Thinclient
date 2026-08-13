#!/bin/bash
# Prove the Shut Down button actually powers the machine off.
#
# Boots the ISO in QEMU, tabs to the Shut Down button, confirms, and checks that
# the virtual machine really powered off. This is the end-to-end test for the
# whole privileged path: GTK handler -> sudo -> systemctl poweroff.
#
#   sudo bash build/shutdowntest.sh [tab-count]
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ISO="$(ls -1 "$REPO"/out/*.iso 2>/dev/null | head -1)"
TABS="${1:-4}"
OUT="$REPO/out/shutdowntest"
MON=/tmp/tc-shutdown-monitor.sock

[ -n "$ISO" ] || { echo "no ISO in $REPO/out"; exit 1; }
rm -rf "$OUT"; mkdir -p "$OUT"; rm -f "$MON"
pkill -f 'tc-shutdown-monitor' 2>/dev/null; sleep 1

ACCEL=(); [ -w /dev/kvm ] && ACCEL=(-enable-kvm -cpu host)

qemu-system-x86_64 "${ACCEL[@]}" -m 2560 -smp 4 \
    -cdrom "$ISO" -boot d -vga std \
    -netdev user,id=net0 -device e1000,netdev=net0 \
    -display none -monitor "unix:$MON,server,nowait" \
    > "$OUT/qemu.log" 2>&1 &
QEMU=$!

cat > /tmp/tc-mon2.py <<'PYEOF'
import socket, sys, time
sock = socket.socket(socket.AF_UNIX); sock.settimeout(10)
sock.connect(sys.argv[1]); time.sleep(0.4)
try: sock.recv(65536)
except OSError: pass
for cmd in sys.argv[2:]:
    sock.sendall((cmd + "\n").encode()); time.sleep(0.45)
time.sleep(0.6)
try: sock.recv(65536)
except OSError: pass
sock.close()
PYEOF

for _ in $(seq 1 30); do [ -S "$MON" ] && break; sleep 1; done
mon() { python3 /tmp/tc-mon2.py "$MON" "$@" >/dev/null 2>&1; }

echo "waiting for the connection manager..."
sleep 40
mon "screendump $OUT/1-manager.ppm"

echo "tabbing to Shut Down ($TABS presses)"
KEYS=(); for _ in $(seq 1 "$TABS"); do KEYS+=("sendkey tab"); done
mon "${KEYS[@]}"
sleep 1
mon "screendump $OUT/2-focused.ppm"

echo "activating the button"
mon "sendkey ret"
sleep 2
mon "screendump $OUT/3-confirm.ppm"

echo "confirming"
mon "sendkey ret"

echo "waiting for power off..."
for i in $(seq 1 40); do
    kill -0 "$QEMU" 2>/dev/null || break
    sleep 1
done

for f in "$OUT"/*.ppm; do
    [ -f "$f" ] && convert "$f" "${f%.ppm}.png" 2>/dev/null && rm -f "$f"
done

if kill -0 "$QEMU" 2>/dev/null; then
    mon "screendump $OUT/4-final.ppm"
    [ -f "$OUT/4-final.ppm" ] && convert "$OUT/4-final.ppm" "$OUT/4-final.png" && rm -f "$OUT/4-final.ppm"
    echo "RESULT: still running - the machine did NOT power off"
    kill -9 "$QEMU" 2>/dev/null
    echo "screenshots in $OUT (check 2-focused.png for where focus landed)"
    exit 1
fi

echo "RESULT: the virtual machine powered off - Shut Down works end to end"
echo "screenshots in $OUT"
exit 0
