#!/bin/bash
# Verify the USB-cache implementation in a completed rootfs/initramfs/PXE tree.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=config.sh
source "$REPO/build/config.sh"
ROOTFS="$WORKDIR/rootfs"
PXE_TREE="${PXE_TREE:-${PXE:-${OUTDIR:-$REPO/out}/pxe}}"
fail=0
ok() { printf '  ok      %s\n' "$1"; }
bad() { printf '  FAIL    %s\n' "$1"; fail=1; }

for file in \
    usr/lib/live/boot/9991-thinclient-cache.sh \
    etc/initramfs-tools/hooks/thinclient-cache \
    usr/local/sbin/tc-cache-save; do
    [ -x "$ROOTFS/$file" ] && ok "$file" || bad "$file is absent or not executable"
done
[ -r "$ROOTFS/etc/systemd/system/tc-cache-save.service" ] \
    && ok "tc-cache-save.service" || bad "tc-cache-save.service is absent"
grep -q '^do_httpmount_network ()' "$ROOTFS/usr/lib/live/boot/9990-mount-http.sh" \
    && ok "Debian HTTP fetcher is wrapped" || bad "Debian HTTP fetcher was not renamed"

INITRD="${INITRD:-$PXE_TREE/thinclient/initrd.img}"
[ -r "$INITRD" ] || { echo "missing exported initramfs: $INITRD" >&2; exit 1; }
if command -v lsinitramfs >/dev/null 2>&1; then
    INITRD_LIST="$(lsinitramfs "$INITRD")"
else
    install -d "$ROOTFS/opt/test-assets"
    cp "$INITRD" "$ROOTFS/opt/test-assets/initrd.img"
    INITRD_GUEST=/opt/test-assets/initrd.img
    INITRD_LIST="$(chroot "$ROOTFS" lsinitramfs "$INITRD_GUEST")"
fi
for pattern in \
    'usr/lib/live/boot/9991-thinclient-cache.sh' \
    'bin/sha256sum' \
    'bin/tee' \
    'bin/cp' \
    '/usb-storage\.ko(\.|$)' \
    '/uas\.ko(\.|$)' \
    '/xhci-pci\.ko(\.|$)' \
    '/ehci-pci\.ko(\.|$)' \
    '/sd_mod\.ko(\.|$)'; do
    grep -Eq "$pattern" <<<"$INITRD_LIST" \
        && ok "$pattern is in initramfs" || bad "$pattern is missing from initramfs"
done

if [ "$CACHE_PROFILE" = lite ]; then
    for module in ata_piix ahci nvme virtio_blk sr_mod; do
        grep -Eq "/${module}\\.ko(\\.|$)" <<<"$INITRD_LIST" \
            && ok "$module supports Lite media boot" || bad "$module is missing from Lite initramfs"
    done
fi

for profile in lite full; do
    directory="$PXE_TREE/thinclient/$profile"
    [ -r "$directory/filesystem.squashfs" ] || continue
    expected="$(sha256sum "$directory/filesystem.squashfs" | awk '{print $1}')"
    sidecar="$(awk 'NR==1 {print $1}' "$directory/filesystem.squashfs.sha256" 2>/dev/null || true)"
    [ "$sidecar" = "$expected" ] \
        && ok "$profile checksum sidecar" || bad "$profile checksum sidecar does not match"
    grep -Rqs "tc.cache.profile=$profile tc.cache.sha256=$expected" \
        "$PXE_TREE/pxelinux.cfg/default" "$PXE_TREE/grub/grub.cfg" \
        && ok "$profile PXE checksum argument" || bad "$profile PXE checksum argument is missing"
done

echo
[ "$fail" -eq 0 ] && echo "USB CACHE CHECK PASSED" || echo "USB CACHE CHECK FAILED"
exit "$fail"
