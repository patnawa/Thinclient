#!/bin/bash
# Verify both final images. Use an isolated PXE copy because boot tests render
# menus for QEMU and deliberately replace central configuration with demo data.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${RELEASE_VERSION:-1.5.1}"
OUTPUT="${RELEASE_OUT:-$REPO/out/release-$VERSION}"
for profile in lite full; do
    case "$profile" in lite) image=thinclient-lite-amd64; conf=0; features=0; modules=list ;; full) image=thinclient-full-drivers-amd64; conf=64; features=1; modules=most ;; esac
    scratch="$(mktemp -d /tmp/thinclient-pxe-test.XXXXXX)"
    trap 'rm -rf -- "$scratch"' EXIT
    cp -a "$OUTPUT/$profile/pxe" "$scratch/pxe"
    TC_CONFIG_LOCAL=/dev/null DISTRO_VERSION="$VERSION-$profile" IMAGE_NAME="$image" \
        OUTDIR="$OUTPUT/$profile" PXE="$scratch/pxe" CACHE_PROFILE="$profile" \
        INITRAMFS_MODULES="$modules" \
        INCLUDE_VNC="$features" INCLUDE_PRINTING="$features" INCLUDE_SMARTCARD="$features" \
        INCLUDE_USB_REDIR="$features" INCLUDE_WIFI="$features" INCLUDE_WIFI_FIRMWARE="$features" \
        INCLUDE_SOF_FIRMWARE="$features" INCLUDE_AMD_MICROCODE="$features" \
        INCLUDE_ADMIN_TOOLS="$features" INCLUDE_SSH_SERVER="$features" \
        TCCONF_SIZE_MB="$conf" bash "$REPO/build/verify-all.sh"
    cp "$scratch/pxe/thinclient/verification.json" "$OUTPUT/$profile/pxe/thinclient/verification.json"
    rm -rf -- "$scratch"
    trap - EXIT
done
