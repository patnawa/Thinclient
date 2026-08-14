#!/bin/bash
# Prove the Shut Down button actually powers the machine off.
#
# Boots the ISO in QEMU, opens Power, chooses Shut down, and checks that
# the virtual machine really powered off. This is the end-to-end test for the
# whole privileged path: GTK handler -> sudo -> systemctl poweroff.
#
#   sudo bash build/shutdowntest.sh [tab-count]
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
source "$REPO/build/config.sh"
ISO="${ISO:-$REPO/out/${IMAGE_NAME}-${DISTRO_VERSION}.iso}"
TABS="${1:-4}"
OUT="$REPO/out/shutdowntest"
MON=/tmp/tc-shutdown-monitor.sock

[ -f "$ISO" ] || { echo "missing $ISO - run build.sh first"; exit 1; }
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
# Full-driver images and old CPUs can spend well over 40 seconds unpacking the
# live root. Sending keys to a black boot screen makes the test fail for the
# wrong reason, so wait for a materially rendered GUI (with a hard deadline).
LAST=0
READY=0
for ELAPSED in 60 75 90 105 120; do
    sleep $((ELAPSED - LAST)); LAST=$ELAPSED
    kill -0 "$QEMU" 2>/dev/null || break
    mon "screendump $OUT/1-manager.ppm"
    BRIGHTNESS=$(convert "$OUT/1-manager.ppm" -format '%[fx:int(mean*255)]' info: 2>/dev/null || echo 0)
    echo "  t=${ELAPSED}s  brightness $BRIGHTNESS"
    if [ "${BRIGHTNESS:-0}" -ge 20 ]; then
        READY=1
        break
    fi
done
if [ "$READY" -ne 1 ]; then
    [ -f "$OUT/1-manager.ppm" ] && convert "$OUT/1-manager.ppm" "$OUT/1-manager.png" 2>/dev/null
    kill -9 "$QEMU" 2>/dev/null
    echo "RESULT: the connection manager did not become ready before the deadline"
    exit 1
fi

echo "tabbing to Power ($TABS presses)"
KEYS=(); for _ in $(seq 1 "$TABS"); do KEYS+=("sendkey tab"); done
mon "${KEYS[@]}"
sleep 1
mon "screendump $OUT/2-focused.ppm"

echo "opening Power options"
mon "sendkey ret"
sleep 2
mon "screendump $OUT/3-power-options.ppm"

echo "choosing Shut down"
mon "sendkey tab" "sendkey tab" "sendkey ret"

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
