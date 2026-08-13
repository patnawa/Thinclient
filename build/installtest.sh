#!/bin/bash
# Prove the installer works: install onto a blank virtual disk from the live
# image, then boot that disk with no installation media attached at all.
#
# Phase 2 is the real test. If the client reaches its session with the ISO gone,
# then partitioning, the squashfs copy and the bootloader all worked.
#
#   sudo bash build/installtest.sh          # BIOS
#   sudo bash build/installtest.sh uefi     # UEFI
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
source "$REPO/build/config.sh"
MODE="${1:-bios}"
ISO="${ISO:-$REPO/out/${IMAGE_NAME}-${DISTRO_VERSION}.iso}"
KERNEL="$REPO/out/pxe/thinclient/vmlinuz"
INITRD="$REPO/out/pxe/thinclient/initrd.img"
OUT="$REPO/out/installtest-$MODE"
DISK="$OUT/target.qcow2"
MON=/tmp/tc-install-monitor.sock

[ -f "$ISO" ] || { echo "missing $ISO - run build.sh first"; exit 1; }
[ -f "$KERNEL" ] && [ -f "$INITRD" ] || { echo "run build.sh first"; exit 1; }

rm -rf "$OUT"; mkdir -p "$OUT"; rm -f "$MON"
pkill -f 'tc-install-monitor' 2>/dev/null; sleep 1

ACCEL=(); [ -w /dev/kvm ] && ACCEL=(-enable-kvm -cpu host)
firmware() {
    if [ "$MODE" = "uefi" ]; then
        cp /usr/share/OVMF/OVMF_VARS_4M.fd "$OUT/vars.fd"
        printf '%s\n' \
            "-drive" "if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd" \
            "-drive" "if=pflash,format=raw,file=$OUT/vars.fd"
    fi
}
mapfile -t FIRMWARE < <(firmware)

cat > /tmp/tc-mon4.py <<'PYEOF'
import socket, sys, time
sock = socket.socket(socket.AF_UNIX); sock.settimeout(15)
sock.connect(sys.argv[1]); time.sleep(0.4)
try: sock.recv(65536)
except OSError: pass
for cmd in sys.argv[2:]:
    sock.sendall((cmd + "\n").encode()); time.sleep(0.5)
time.sleep(0.8)
sock.close()
PYEOF
mon() { python3 /tmp/tc-mon4.py "$MON" "$@" >/dev/null 2>&1; }
shoot() {
    mon "screendump $OUT/$1.ppm"
    [ -f "$OUT/$1.ppm" ] && convert "$OUT/$1.ppm" "$OUT/$1.png" 2>/dev/null && rm -f "$OUT/$1.ppm"
}

echo "creating a blank 8G disk"
qemu-img create -f qcow2 "$DISK" 8G >/dev/null

# ============================================================= phase 1 =======
# Boot the live system with the unattended-install arguments. Booting the kernel
# directly keeps this deterministic - no boot menu timing, no keystrokes.
#
# The ISO is attached as a virtio disk rather than as a CD. That is what a USB
# stick looks like to live-boot - the isohybrid image written raw to a block
# device - so it is the more realistic shape anyway, and it sidesteps an OVMF
# quirk where an emulated IDE CD does not reach the guest under direct kernel
# boot. Booting the ISO the normal way on UEFI is covered by boottest.sh.
#
# Drive order matters: the blank target is first (/dev/vda), the ISO second
# (/dev/vdb). tc-install refuses to install onto the medium it booted from, so
# the two must not be swapped.
# =============================================================================
echo
echo "phase 1: unattended install to /dev/vda"
qemu-system-x86_64 "${ACCEL[@]}" ${FIRMWARE+"${FIRMWARE[@]}"} \
    -m 2560 -smp 4 \
    -kernel "$KERNEL" -initrd "$INITRD" \
    -append "boot=live components union=overlay tc.install.auto=1 tc.install.target=/dev/vda console=tty0 console=ttyS0,115200" \
    -drive "file=$DISK,format=qcow2,if=virtio" \
    -drive "file=$ISO,format=raw,if=virtio,readonly=on" \
    -vga std -netdev user,id=n0 -device e1000,netdev=n0 \
    -display none -monitor "unix:$MON,server,nowait" \
    -serial "file:$OUT/install-serial.log" \
    > "$OUT/qemu-install.log" 2>&1 &
QEMU=$!
for _ in $(seq 1 30); do [ -S "$MON" ] && break; sleep 1; done

echo "  installing..."
for i in $(seq 1 60); do
    grep -q 'TC_INSTALL_EXIT=' "$OUT/install-serial.log" 2>/dev/null && break
    kill -0 "$QEMU" 2>/dev/null || break
    sleep 5
done
shoot 1-installer

RESULT="$(grep -o 'TC_INSTALL_EXIT=[0-9]*' "$OUT/install-serial.log" 2>/dev/null | tail -1)"
sed 's/\r//' "$OUT/install-serial.log" 2>/dev/null |
    grep -E 'target:|copying|installing the|partitioning|creating file|installation (finished|failed)|settings written|GB|FATAL|Traceback|Error' |
    tail -14 | sed 's/^/    /'
echo "  ${RESULT:-TC_INSTALL_EXIT=<none>}"

mon "quit"; sleep 2; kill -9 "$QEMU" 2>/dev/null; rm -f "$MON"

if [ "$RESULT" != "TC_INSTALL_EXIT=0" ]; then
    echo
    echo "RESULT: install FAILED - see $OUT/install-serial.log"
    exit 1
fi

# ============================================================= phase 2 =======
# No -cdrom, no -kernel: the firmware must find and boot the installed disk.
# =============================================================================
echo
echo "phase 2: booting the installed disk, no media attached"
mapfile -t FIRMWARE < <(firmware)          # fresh NVRAM, so no cached boot entry

qemu-system-x86_64 "${ACCEL[@]}" ${FIRMWARE+"${FIRMWARE[@]}"} \
    -m 2560 -smp 4 \
    -drive "file=$DISK,format=qcow2,if=virtio" -boot c \
    -vga std -netdev user,id=n0 -device e1000,netdev=n0 \
    -display none -monitor "unix:$MON,server,nowait" \
    -serial "file:$OUT/boot-serial.log" \
    > "$OUT/qemu-boot.log" 2>&1 &
QEMU=$!
for _ in $(seq 1 30); do [ -S "$MON" ] && break; sleep 1; done

LAST=0; BEST=0
for T in 20 40 60 90; do
    sleep $((T - LAST)); LAST=$T
    kill -0 "$QEMU" 2>/dev/null || { echo "  qemu exited at ${T}s"; break; }
    shoot "installed-t$T"
    mean=$(convert "$OUT/installed-t$T.png" -format '%[fx:int(mean*255)]' info: 2>/dev/null || echo 0)
    echo "  t=${T}s  brightness $mean"
    [ "${mean:-0}" -gt "$BEST" ] && BEST=$mean
done

mon "quit"; sleep 2; kill -9 "$QEMU" 2>/dev/null

echo
if [ "$BEST" -ge 20 ]; then
    echo "RESULT: the installed disk booted to a graphical session"
else
    echo "RESULT: the installed disk did NOT reach a session (max brightness $BEST)"
    echo "  check $OUT/installed-t90.png and $OUT/boot-serial.log"
    exit 1
fi
echo "screenshots in $OUT"
