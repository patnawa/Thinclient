#!/bin/bash
# Merge independently built Full and Lite trees into one BIOS/UEFI PXE menu.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
FULL="${1:-$REPO/out/full/pxe}"
LITE="${2:-$REPO/out/lite/pxe}"
OUT="${3:-$REPO/out/pxe-dual}"

die() { printf 'merge-pxe-profiles.sh: %s\n' "$*" >&2; exit 1; }
config_for_tree() {
    local tree="$1"
    if [ -r "$tree/config.json" ]; then
        printf '%s\n' "$tree/config.json"
    elif [ -r "$(dirname "$tree")/config.json" ]; then
        printf '%s\n' "$(dirname "$tree")/config.json"
    else
        die "missing config.json in $tree or $(dirname "$tree")"
    fi
}

for tree in "$FULL" "$LITE"; do
    [ -r "$tree/thinclient/vmlinuz" ] || die "missing kernel in $tree"
    [ -r "$tree/thinclient/initrd.img" ] || die "missing initrd in $tree"
    [ -r "$tree/thinclient/filesystem.squashfs" ] || die "missing squashfs in $tree"
done
[ ! -e "$OUT" ] || die "output already exists: $OUT"
FULL_CONFIG="$(config_for_tree "$FULL")"

mkdir -p "$(dirname "$OUT")"
cp -a "$LITE" "$OUT"
mv "$OUT/thinclient" "$OUT/thinclient-lite"
mkdir "$OUT/thinclient"
mv "$OUT/thinclient-lite" "$OUT/thinclient/lite"
cp -a "$FULL/thinclient" "$OUT/thinclient/full"

# Prefer the full build's exported central configuration. Both profiles still
# request the same URL, so per-device settings behave identically.
cp "$FULL_CONFIG" "$OUT/config.json"

# shellcheck source=config.sh
source "$HERE/config.sh"
printf '%s' "$CACHE_LABEL" | grep -Eq '^[A-Za-z0-9._-]{1,32}$' \
    || die "invalid CACHE_LABEL: $CACHE_LABEL"
LITE_SHA="$(sha256sum "$OUT/thinclient/lite/filesystem.squashfs" | awk '{print $1}')"
FULL_SHA="$(sha256sum "$OUT/thinclient/full/filesystem.squashfs" | awk '{print $1}')"
printf '%s  filesystem.squashfs\n' "$LITE_SHA" \
    > "$OUT/thinclient/lite/filesystem.squashfs.sha256"
printf '%s  filesystem.squashfs\n' "$FULL_SHA" \
    > "$OUT/thinclient/full/filesystem.squashfs.sha256"
LITE_CACHE="tc.cache=1 tc.cache.label=$CACHE_LABEL tc.cache.profile=lite tc.cache.sha256=$LITE_SHA"
FULL_CACHE="tc.cache=1 tc.cache.label=$CACHE_LABEL tc.cache.profile=full tc.cache.sha256=$FULL_SHA"

cat > "$OUT/pxelinux.cfg/default" <<EOF
DEFAULT menu.c32
PROMPT 0
TIMEOUT 50
MENU TITLE $DISTRO_NAME dual-profile network boot

LABEL lite-cache
  MENU LABEL ^Lite Auto Cache - best for group boot (recommended)
  MENU DEFAULT
  KERNEL thinclient/lite/vmlinuz
  APPEND initrd=thinclient/lite/initrd.img $KERNEL_CMDLINE $LITE_CACHE ip=dhcp fetch=http://{{HTTP}}/thinclient/lite/filesystem.squashfs tc.config=http://{{HTTP}}/config.json

LABEL lite-network
  MENU LABEL Lite ^Network Only - bypass a slow cache USB
  KERNEL thinclient/lite/vmlinuz
  APPEND initrd=thinclient/lite/initrd.img $KERNEL_CMDLINE ip=dhcp fetch=http://{{HTTP}}/thinclient/lite/filesystem.squashfs tc.config=http://{{HTTP}}/config.json

LABEL full
  MENU LABEL ^Full Drivers Auto Cache - Wi-Fi and uncommon hardware
  KERNEL thinclient/full/vmlinuz
  APPEND initrd=thinclient/full/initrd.img $KERNEL_CMDLINE $FULL_CACHE ip=dhcp fetch=http://{{HTTP}}/thinclient/full/filesystem.squashfs tc.config=http://{{HTTP}}/config.json

LABEL debug-lite
  MENU LABEL Lite diagnostic console
  KERNEL thinclient/lite/vmlinuz
  APPEND initrd=thinclient/lite/initrd.img boot=live components union=overlay tc.debug=1 $LITE_CACHE ip=dhcp fetch=http://{{HTTP}}/thinclient/lite/filesystem.squashfs tc.config=http://{{HTTP}}/config.json
EOF

cat > "$OUT/grub/grub.cfg" <<EOF
set default=0
set fallback=3
set timeout=5
set timeout_style=menu
# UEFI firmware only uses TFTP for the small GRUB loader.  Kernel and initrd
# use HTTP by default because firmware TFTP is painfully slow on older PCs.
# If HTTP is unavailable GRUB falls back to entry 3, the equivalent TFTP boot.
menuentry "Lite Auto Cache - HTTP fast path (recommended)" {
    linux (http,{{HTTP}})/thinclient/lite/vmlinuz $KERNEL_CMDLINE $LITE_CACHE ip=dhcp fetch=http://{{HTTP}}/thinclient/lite/filesystem.squashfs tc.config=http://{{HTTP}}/config.json
    initrd (http,{{HTTP}})/thinclient/lite/initrd.img
}
menuentry "Lite Network Only - HTTP fast path" {
    linux (http,{{HTTP}})/thinclient/lite/vmlinuz $KERNEL_CMDLINE ip=dhcp fetch=http://{{HTTP}}/thinclient/lite/filesystem.squashfs tc.config=http://{{HTTP}}/config.json
    initrd (http,{{HTTP}})/thinclient/lite/initrd.img
}
menuentry "Full Drivers Auto Cache - HTTP fast path" {
    linux (http,{{HTTP}})/thinclient/full/vmlinuz $KERNEL_CMDLINE $FULL_CACHE ip=dhcp fetch=http://{{HTTP}}/thinclient/full/filesystem.squashfs tc.config=http://{{HTTP}}/config.json
    initrd (http,{{HTTP}})/thinclient/full/initrd.img
}
menuentry "Lite Auto Cache - TFTP recovery" {
    linux /thinclient/lite/vmlinuz $KERNEL_CMDLINE $LITE_CACHE ip=dhcp fetch=http://{{HTTP}}/thinclient/lite/filesystem.squashfs tc.config=http://{{HTTP}}/config.json
    initrd /thinclient/lite/initrd.img
}
menuentry "Lite Network Only - TFTP recovery" {
    linux /thinclient/lite/vmlinuz $KERNEL_CMDLINE ip=dhcp fetch=http://{{HTTP}}/thinclient/lite/filesystem.squashfs tc.config=http://{{HTTP}}/config.json
    initrd /thinclient/lite/initrd.img
}
menuentry "Full Drivers Auto Cache - TFTP recovery" {
    linux /thinclient/full/vmlinuz $KERNEL_CMDLINE $FULL_CACHE ip=dhcp fetch=http://{{HTTP}}/thinclient/full/filesystem.squashfs tc.config=http://{{HTTP}}/config.json
    initrd /thinclient/full/initrd.img
}
EOF

cat > "$OUT/boot.ipxe" <<EOF
#!ipxe
dhcp
menu $DISTRO_NAME network boot
item --default lite-cache Lite Auto Cache - best for group boot (recommended)
item lite-network Lite Network Only - bypass a slow cache USB
item full Full Drivers Auto Cache - Wi-Fi and uncommon hardware
choose --default lite-cache --timeout 5000 profile || goto lite-cache
goto \${profile}

:lite-cache
set base http://{{HTTP}}/thinclient/lite
set cacheargs $LITE_CACHE
goto boot

:lite-network
set base http://{{HTTP}}/thinclient/lite
clear cacheargs
goto boot

:full
set base http://{{HTTP}}/thinclient/full
set cacheargs $FULL_CACHE

:boot
kernel \${base}/vmlinuz $KERNEL_CMDLINE \${cacheargs} ip=dhcp fetch=\${base}/filesystem.squashfs tc.config=http://{{HTTP}}/config.json
initrd \${base}/initrd.img
boot
EOF

printf 'Dual-profile PXE tree created at %s\n' "$OUT"
printf '  Lite: %s  (%s)\n  Full: %s  (%s)\n' "$LITE" "$LITE_SHA" "$FULL" "$FULL_SHA"
