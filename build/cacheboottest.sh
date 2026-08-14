#!/bin/bash
# Prove a first PXE boot fills TCCACHE and a second boot skips the HTTP root.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PXE="${PXE:-$REPO/out/pxe-dual}"
OUT="${CACHETEST_OUT:-$REPO/out/cacheboottest}"
CACHE_IMAGE="$OUT/tccache.img"
PROFILE="${CACHE_PROFILE:-lite}"
SIZE_MB="${CACHE_SIZE_MB:-1024}"

for tool in qemu-system-x86_64 mkfs.vfat mdir; do
    command -v "$tool" >/dev/null || { echo "missing test tool: $tool" >&2; exit 2; }
done
[ -r "$PXE/thinclient/$PROFILE/filesystem.squashfs" ] \
    || { echo "missing $PROFILE profile in $PXE" >&2; exit 1; }

mkdir -p "$OUT"
rm -f "$CACHE_IMAGE"
mkfs.vfat -C -F 32 -n TCCACHE "$CACHE_IMAGE" $((SIZE_MB * 1024)) >/dev/null
EXTRA="-drive if=none,id=tccache,file=$CACHE_IMAGE,format=raw -device qemu-xhci,id=xhci -device usb-storage,drive=tccache,bus=xhci.0"

echo "=== first boot: network fetch and cache fill ==="
PXE="$PXE" PXETEST_OUT="$OUT/first" CONFIG_SOURCE="$PXE/config.json" \
QEMU_EXTRA_ARGS="$EXTRA" EXPECT_SQUASH=1 bash "$REPO/build/pxetest.sh" bios

EXPECTED_SHA="$(sha256sum "$PXE/thinclient/$PROFILE/filesystem.squashfs" | awk '{print $1}')"
mdir -b -i "$CACHE_IMAGE" "::/thinclient-cache/$PROFILE" 2>/dev/null \
    | grep -q "/$EXPECTED_SHA.squashfs$" || {
        echo "expected checksum-addressed cache file is missing" >&2
        exit 1
    }
echo "  ok    verified squashfs was written to TCCACHE"

echo
echo "=== second boot: cache hit, no HTTP squashfs ==="
PXE="$PXE" PXETEST_OUT="$OUT/second" CONFIG_SOURCE="$PXE/config.json" \
QEMU_EXTRA_ARGS="$EXTRA" EXPECT_SQUASH=0 bash "$REPO/build/pxetest.sh" bios

echo
echo "USB CACHE BOOT TEST PASSED"
