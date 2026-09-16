#!/bin/bash
# Supplemental VM coverage, not certification of physical NICs/GPU/firmware.
# Run sequentially: existing QEMU helpers use shared monitor paths.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
source "$REPO/build/config.sh"
export DISTRO_VERSION IMAGE_NAME
export OUTDIR="${OUTDIR:-$REPO/out}"
export ISO="${ISO:-$OUTDIR/$IMAGE_NAME-$DISTRO_VERSION.iso}"
OUTPUT="${MATRIX_OUT:-$OUTDIR/hardware-matrix}"
[ ! -e "$OUTPUT" ] || { echo "matrix output already exists: $OUTPUT" >&2; exit 2; }
mkdir -p "$OUTPUT"
PXE_SOURCE="${PXE:-$OUTDIR/pxe}"
scratch="$(mktemp -d /var/tmp/thinclient-hardware-matrix.XXXXXX)"
trap 'rm -rf -- "$scratch"' EXIT
cp -a "$PXE_SOURCE" "$scratch/pxe"
export PXE="$scratch/pxe" CONFIG_SOURCE="${CONFIG_SOURCE:-$OUTDIR/config.json}"
failed=0
run_case() {
    local name="$1"; shift
    echo "=== hardware matrix: $name ==="
    if "$@" > "$OUTPUT/$name.log" 2>&1; then
        printf '{"case":"%s","passed":true}\n' "$name" >> "$OUTPUT/results.jsonl"
    else
        printf '{"case":"%s","passed":false}\n' "$name" >> "$OUTPUT/results.jsonl"
        tail -15 "$OUTPUT/$name.log"
        failed=$((failed + 1))
    fi
}
run_case bios-2g-baseline-cpu env TC_TEST_RAM_MB=2048 TC_TEST_CPU=qemu64 TC_TEST_CPUS=2 \
    BOOTTEST_OUT="$OUTPUT/bios-2g-baseline-cpu" bash "$REPO/build/boottest.sh" bios
run_case uefi-2g-virtio-gpu env TC_TEST_RAM_MB=2048 TC_TEST_VGA=virtio \
    BOOTTEST_OUT="$OUTPUT/uefi-2g-virtio-gpu" bash "$REPO/build/boottest.sh" uefi
run_case pxe-2g-rtl8139 env TC_TEST_RAM_MB=2048 TC_TEST_NIC=rtl8139 \
    PXETEST_OUT="$OUTPUT/pxe-2g-rtl8139" bash "$REPO/build/pxetest.sh" bios
run_case pxe-2g-virtio env TC_TEST_RAM_MB=2048 TC_TEST_NIC=virtio-net-pci \
    PXETEST_OUT="$OUTPUT/pxe-2g-virtio" bash "$REPO/build/pxetest.sh" uefi
run_case install-sata-bios env TC_TEST_RAM_MB=2048 TC_TEST_DISK_BUS=sata \
    INSTALLTEST_OUT="$OUTPUT/install-sata-bios" bash "$REPO/build/installtest.sh" bios
run_case install-nvme-uefi env TC_TEST_RAM_MB=2048 TC_TEST_DISK_BUS=nvme \
    INSTALLTEST_OUT="$OUTPUT/install-nvme-uefi" bash "$REPO/build/installtest.sh" uefi
run_case install-secureboot env TC_TEST_RAM_MB=2048 \
    INSTALLTEST_OUT="$OUTPUT/install-secureboot" bash "$REPO/build/installtest.sh" secureboot
if [ "${MATRIX_EXPERIMENTAL_1G:-0}" = 1 ]; then
    run_case experimental-pxe-1g env TC_TEST_RAM_MB=1024 \
        PXETEST_OUT="$OUTPUT/experimental-pxe-1g" bash "$REPO/build/pxetest.sh" bios
fi
echo "Hardware matrix failed cases: $failed"
exit "$failed"
