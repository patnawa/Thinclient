#!/bin/bash
# Static release gate for the adapter drivers/firmware a network appliance
# depends on. QEMU cannot emulate representative Wi-Fi hardware, so inspect the
# actual rootfs and initramfs assembled for the image.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
source "$REPO/build/config.sh"
ROOTFS="$WORKDIR/rootfs"
[ -d "$ROOTFS" ] || { echo "no rootfs at $ROOTFS - run build.sh first"; exit 1; }

fail=0
ok() { printf '  ok      %s\n' "$1"; }
bad() { printf '  FAIL    %s\n' "$1"; fail=1; }

has_package() {
    chroot "$ROOTFS" dpkg-query -W -f='${db:Status-Status}' "$1" 2>/dev/null \
      | grep -qx installed
}
FIRMWARE_LIST="$(find "$ROOTFS/usr/lib/firmware" \( -type f -o -type l \) -print 2>/dev/null)"
has_file() { grep -Eq "$1" <<<"$FIRMWARE_LIST"; }

echo "=== common wired and USB Ethernet drivers ==="
for module in e1000e igb igc r8169 tg3 bnx2 alx r8152 ax88179_178a \
              asix cdc_ether cdc_ncm virtio_net hv_netvsc vmxnet3; do
    if [ -n "$(find "$ROOTFS/lib/modules" -type f -name "$module.ko*" -print -quit)" ]; then
        ok "$module"
    else
        bad "$module is absent from the kernel"
    fi
done

echo
echo "=== early-boot network drivers ==="
INITRD="$(ls -1 "$ROOTFS"/boot/initrd.img-* | sort -V | tail -1)"
if command -v lsinitramfs >/dev/null 2>&1; then
    INITRD_LIST="$(lsinitramfs "$INITRD")"
else
    # The Windows/WSL build host may not have initramfs-tools installed even
    # though the assembled client does. Inspect it with the image's own tool.
    INITRD_GUEST="${INITRD#"$ROOTFS"}"
    INITRD_LIST="$(chroot "$ROOTFS" lsinitramfs "$INITRD_GUEST")"
fi
for module in e1000e r8169 r8152 ax88179_178a cdc_ncm; do
    if grep -Eq "/${module}\.ko(\.|$)" <<<"$INITRD_LIST"; then
        ok "$module is in initramfs"
    else
        bad "$module is missing from initramfs"
    fi
done

echo
echo "=== wired firmware ==="
for package in firmware-realtek firmware-bnx2 firmware-bnx2x firmware-misc-nonfree; do
    has_package "$package" && ok "$package" || bad "$package is not installed"
done
has_file '/tigon/tg3.*\.bin$' && ok "Broadcom tg3 firmware" || bad "tg3 firmware is missing"
has_file '/bnx2/.*\.fw$' && ok "Broadcom bnx2 firmware" || bad "bnx2 firmware is missing"
has_file '/bnx2x/.*\.fw$' && ok "Broadcom bnx2x firmware" || bad "bnx2x firmware is missing"

if [ "$INCLUDE_WIFI" = "1" ]; then
    echo
    echo "=== Wi-Fi stack ==="
    for package in wpasupplicant iw wireless-regdb; do
        has_package "$package" && ok "$package" || bad "$package is not installed"
    done
fi

if [ "$INCLUDE_WIFI_FIRMWARE" = "1" ]; then
    echo
    echo "=== Wi-Fi firmware ==="
    for package in firmware-iwlwifi firmware-atheros firmware-brcm80211 firmware-mediatek; do
        has_package "$package" && ok "$package" || bad "$package is not installed"
    done
    has_file '/iwlwifi-.*\.ucode$' && ok "Intel Wi-Fi" || bad "Intel Wi-Fi firmware is missing"
    has_file '/ath10k/.*/firmware.*\.bin$' && ok "Qualcomm/Atheros Wi-Fi" || bad "Atheros firmware is missing"
    has_file '/brcm/brcmfmac.*\.bin$' && ok "Broadcom/Cypress Wi-Fi" || bad "Broadcom Wi-Fi firmware is missing"
    has_file '/mediatek/.*\.(bin|rom)$' && ok "MediaTek Wi-Fi" || bad "MediaTek firmware is missing"
fi

echo
[ "$fail" -eq 0 ] && echo "NETWORK ADAPTER CHECK PASSED" || echo "NETWORK ADAPTER CHECK FAILED"
exit "$fail"
