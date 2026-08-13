#!/bin/bash
# Prove network boot end to end, without touching a production LAN.
#
# QEMU's user-mode network has a built-in DHCP and TFTP server, so a guest can
# PXE boot entirely inside it. The squashfs and the central configuration come
# over HTTP from a real server process on the host, exactly as they would from
# a deployment server.
#
# What this proves:
#   1. the client gets a DHCP lease and TFTPs pxelinux + kernel + initrd
#   2. it fetches the roughly 499 MiB squashfs over HTTP and runs from RAM
#   3. it pulls central configuration and uses it in preference to the built-in
#
#   sudo bash build/pxetest.sh           # BIOS clients (pxelinux)
#   sudo bash build/pxetest.sh uefi      # UEFI clients (GRUB netboot)
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-bios}"
PXE="$REPO/out/pxe"
OUT="$REPO/out/pxetest-$MODE"
MON=/tmp/tc-pxe-monitor.sock
PORT=8087

# BIOS firmware chainloads pxelinux; UEFI firmware needs a GRUB image built for
# the network, which is a different file and a different boot path entirely.
if [ "$MODE" = "uefi" ]; then
    BOOTFILE="grub/x86_64-efi/core.efi"
else
    BOOTFILE="pxelinux.0"
fi

[ -f "$PXE/$BOOTFILE" ] || { echo "missing $PXE/$BOOTFILE - run build.sh first"; exit 1; }
rm -rf "$OUT"; mkdir -p "$OUT"; rm -f "$MON"
pkill -f 'tc-pxe-monitor' 2>/dev/null
pkill -f "tc-config-server.py --root $PXE" 2>/dev/null
sleep 1

# --- point the boot files at the host as QEMU's guest sees it ----------------
# 10.0.2.2 is the host from inside QEMU user networking.
bash "$PXE/render-configs.sh" "10.0.2.2:$PORT" >/dev/null
grep -q '{{HTTP}}' "$PXE/pxelinux.cfg/default" && {
    echo "FAIL: the boot files still contain {{HTTP}}"; exit 1; }

# --- central configuration, deliberately distinguishable --------------------
# If the client shows this name, it can only have come from the server: it is
# not the name compiled into the image.
python3 - "$REPO/out/config.json" "$PXE/config.json" <<'PYEOF'
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
cfg["connections"][0]["name"] = "CENTRAL CONFIG OK"
cfg["device"]["screen_blank_minutes"] = 0
json.dump(cfg, open(sys.argv[2], "w", encoding="utf-8"), indent=2)
PYEOF
echo "central config: $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["connections"][0]["name"])' "$PXE/config.json")"

# --- the deployment server --------------------------------------------------
python3 "$REPO/tools/tc-config-server.py" --root "$PXE" --port "$PORT" \
    > "$OUT/server.log" 2>&1 &
SERVER=$!
sleep 2
kill -0 "$SERVER" 2>/dev/null || { echo "config server failed:"; cat "$OUT/server.log"; exit 1; }

cleanup() {
    kill "$SERVER" 2>/dev/null
    [ -n "${QEMU:-}" ] && kill -9 "$QEMU" 2>/dev/null
}
trap cleanup EXIT

cat > /tmp/tc-mon6.py <<'PYEOF'
import socket, sys, time
s = socket.socket(socket.AF_UNIX); s.settimeout(15); s.connect(sys.argv[1])
time.sleep(0.4)
try: s.recv(65536)
except OSError: pass
for cmd in sys.argv[2:]:
    s.sendall((cmd + "\n").encode()); time.sleep(0.5)
time.sleep(0.8); s.close()
PYEOF
mon() { python3 /tmp/tc-mon6.py "$MON" "$@" >/dev/null 2>&1; }
shoot() {
    mon "screendump $OUT/$1.ppm"
    [ -f "$OUT/$1.ppm" ] && convert "$OUT/$1.ppm" "$OUT/$1.png" 2>/dev/null && rm -f "$OUT/$1.ppm"
}

# --- boot a diskless client over the network --------------------------------
ACCEL=(); [ -w /dev/kvm ] && ACCEL=(-enable-kvm -cpu host)

FIRMWARE=()
if [ "$MODE" = "uefi" ]; then
    cp /usr/share/OVMF/OVMF_VARS_4M.fd "$OUT/vars.fd"
    FIRMWARE=(-drive "if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd"
              -drive "if=pflash,format=raw,file=$OUT/vars.fd")
fi

echo
echo "network booting a diskless client ($MODE, boot file: $BOOTFILE)"
qemu-system-x86_64 "${ACCEL[@]}" ${FIRMWARE+"${FIRMWARE[@]}"} \
    -m 2560 -smp 4 \
    -netdev "user,id=n0,tftp=$PXE,bootfile=$BOOTFILE" \
    -device e1000,netdev=n0,bootindex=0 \
    -boot n \
    -vga std -display none \
    -monitor "unix:$MON,server,nowait" \
    -serial "file:$OUT/serial.log" \
    > "$OUT/qemu.log" 2>&1 &
QEMU=$!
for _ in $(seq 1 30); do [ -S "$MON" ] && break; sleep 1; done

LAST=0; BEST=0
for T in 20 40 60 90 120; do
    sleep $((T - LAST)); LAST=$T
    kill -0 "$QEMU" 2>/dev/null || { echo "  qemu exited at ${T}s"; break; }
    shoot "t$T"
    mean=$(convert "$OUT/t$T.png" -format '%[fx:int(mean*255)]' info: 2>/dev/null || echo 0)
    echo "  t=${T}s  brightness $mean"
    [ "${mean:-0}" -gt "$BEST" ] && BEST=$mean
done

mon "quit"; sleep 2

# --- what the server was actually asked for ---------------------------------
echo
echo "requests the deployment server served:"
grep -oE '"GET [^"]+"' "$OUT/server.log" 2>/dev/null | sort | uniq -c | sed 's/^/    /'

# grep -c exits non-zero on zero matches, so a "|| echo 0" fallback appends a
# second line and the arithmetic below breaks. Count lines instead.
SQUASH_SERVED=$(grep -oE 'filesystem\.squashfs' "$OUT/server.log" 2>/dev/null | wc -l)
CONFIG_SERVED=$(grep -oE 'config[^ ]*\.json' "$OUT/server.log" 2>/dev/null | wc -l)
CLIENT_MAC=$(grep -oE '([0-9a-f]{2}:){5}[0-9a-f]{2}' "$OUT/server.log" 2>/dev/null | head -1)

echo
fail=0
[ "$SQUASH_SERVED" -ge 1 ] && echo "  ok    the client fetched the squashfs over HTTP" \
    || { echo "  FAIL  the squashfs was never requested"; fail=1; }
[ "$CONFIG_SERVED" -ge 1 ] && echo "  ok    the client fetched central configuration" \
    || { echo "  FAIL  central configuration was never requested"; fail=1; }
[ -n "$CLIENT_MAC" ] && echo "  ok    it identified itself as $CLIENT_MAC (per-device config would work)" \
    || echo "  note  no MAC header seen"
[ "$BEST" -ge 20 ] && echo "  ok    it reached a graphical session" \
    || { echo "  FAIL  it never reached a session (max brightness $BEST)"; fail=1; }

echo
if [ "$fail" -eq 0 ]; then
    echo "RESULT: network boot works end to end."
    echo "Check $OUT/t120.png - the connection should be named CENTRAL CONFIG OK,"
    echo "which proves the setting came from the server and not from the image."
else
    echo "RESULT: FAILED - see $OUT/serial.log and $OUT/server.log"
fi
exit "$fail"
