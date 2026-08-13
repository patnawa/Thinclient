#!/bin/bash
# Boot the built ISO in QEMU and screenshot it at intervals, so a build can be
# verified end to end without touching hardware.
#
#   bash build/boottest.sh                 # BIOS boot
#   bash build/boottest.sh uefi            # UEFI boot (OVMF)
#   bash build/boottest.sh secureboot      # UEFI with Secure Boot enforced,
#                                          #   Microsoft keys enrolled
#   bash build/boottest.sh debug           # BIOS + the diagnostic entry, which
#                                          #   prints boot timings to the serial log
#
# Needs: qemu-system-x86, ovmf (for uefi), imagemagick
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
# Select the artifact for the configured release explicitly. A glob sorted by
# filename can silently boot an older ISO left in out/ after a version bump.
source "$REPO/build/config.sh"
MODE="${1:-bios}"
ISO="${ISO:-$REPO/out/${IMAGE_NAME}-${DISTRO_VERSION}.iso}"
OUT="$REPO/out/boottest-$MODE"
MON=/tmp/tc-qemu-monitor.sock

[ -f "$ISO" ] || { echo "missing $ISO - run build.sh first"; exit 1; }
command -v qemu-system-x86_64 >/dev/null || { echo "install qemu-system-x86"; exit 1; }

rm -rf "$OUT"; mkdir -p "$OUT"
rm -f "$MON"
pkill -f 'qemu-system-x86_64.*tc-qemu-monitor' 2>/dev/null; sleep 1

ACCEL=()
[ -w /dev/kvm ] && ACCEL=(-enable-kvm -cpu host) && echo "using KVM"

FIRMWARE=()
if [ "$MODE" = "uefi" ] || [ "$MODE" = "secureboot" ]; then
    if [ "$MODE" = "secureboot" ]; then
        # .secboot enforces signature checks; .ms vars ship with Microsoft's
        # keys enrolled, which is what a factory-default PC looks like.
        CODE=/usr/share/OVMF/OVMF_CODE_4M.secboot.fd
        VARS=/usr/share/OVMF/OVMF_VARS_4M.ms.fd
    else
        CODE=$(ls /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_CODE.fd 2>/dev/null | head -1)
        VARS=$(ls /usr/share/OVMF/OVMF_VARS_4M.fd /usr/share/OVMF/OVMF_VARS.fd 2>/dev/null | head -1)
    fi
    [ -f "$CODE" ] && [ -f "$VARS" ] || { echo "missing OVMF firmware: $CODE / $VARS"; exit 1; }
    cp "$VARS" "$OUT/vars.fd"
    FIRMWARE=(-drive "if=pflash,format=raw,readonly=on,file=$CODE"
              -drive "if=pflash,format=raw,file=$OUT/vars.fd"
              -machine q35,smm=on -global driver=cfi.pflash01,property=secure,value=on)
    echo "using OVMF: $CODE"
fi

# Debug mode boots the kernel directly rather than driving the boot menu with
# timed keystrokes. Same kernel, initrd and squashfs; deterministic, and the
# console goes to the serial log where tc-diag prints the boot timings.
DIRECT=()
if [ "$MODE" = "debug" ]; then
    KERNEL="$REPO/out/pxe/thinclient/vmlinuz"
    INITRD="$REPO/out/pxe/thinclient/initrd.img"
    [ -f "$KERNEL" ] && [ -f "$INITRD" ] || {
        echo "debug mode needs out/pxe/ - run build.sh first"; exit 1; }
    DIRECT=(-kernel "$KERNEL" -initrd "$INITRD"
            -append "boot=live components union=overlay tc.debug=1 console=tty0 console=ttyS0,115200")
fi

echo "booting $(basename "$ISO") in $MODE mode..."
qemu-system-x86_64 \
    "${ACCEL[@]}" "${FIRMWARE[@]}" "${DIRECT[@]}" \
    -m 2560 -smp 4 \
    -cdrom "$ISO" -boot d \
    -vga std \
    -netdev user,id=net0 -device e1000,netdev=net0 \
    -display none \
    -monitor "unix:$MON,server,nowait" \
    -serial "file:$OUT/serial.log" \
    > "$OUT/qemu.log" 2>&1 &
QEMU=$!

cat > /tmp/tc-mon.py <<'PYEOF'
import socket, sys, time
sock = socket.socket(socket.AF_UNIX)
sock.settimeout(10)
sock.connect(sys.argv[1])
time.sleep(0.4)
try: sock.recv(65536)
except OSError: pass
sock.sendall((sys.argv[2] + "\n").encode())
time.sleep(1.2)
try: print(sock.recv(65536).decode(errors="replace").strip())
except OSError: pass
sock.close()
PYEOF

# Wait for the monitor socket to appear.
for _ in $(seq 1 30); do [ -S "$MON" ] && break; sleep 1; done
[ -S "$MON" ] || { echo "qemu did not start - see $OUT/qemu.log"; cat "$OUT/qemu.log"; exit 1; }

shoot() {
    local at="$1"
    python3 /tmp/tc-mon.py "$MON" "screendump $OUT/t$at.ppm" >/dev/null 2>&1
    if [ -f "$OUT/t$at.ppm" ]; then
        convert "$OUT/t$at.ppm" "$OUT/t$at.png" 2>/dev/null && rm -f "$OUT/t$at.ppm"
        local mean
        mean=$(convert "$OUT/t$at.png" -format '%[fx:int(mean*255)]' info: 2>/dev/null)
        echo "  t=${at}s  captured (mean brightness $mean)"
    else
        echo "  t=${at}s  screendump failed"
    fi
}

LAST=0
for T in 15 30 45 60 90 120 150; do
    sleep $((T - LAST)); LAST=$T
    kill -0 "$QEMU" 2>/dev/null || { echo "qemu exited early"; break; }
    shoot "$T"
done

echo
echo "shutting down"
python3 /tmp/tc-mon.py "$MON" "quit" >/dev/null 2>&1
sleep 2
kill -9 "$QEMU" 2>/dev/null

echo "screenshots: $OUT/"
ls -1 "$OUT"/*.png 2>/dev/null
exit 0
