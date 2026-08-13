#!/bin/bash
# Build the UEFI network-boot loaders for the PXE tree.
#
# BIOS clients use pxelinux.0, which the image build already exported. UEFI
# clients need a GRUB built for netboot, and - if you want Secure Boot to stay
# enabled - Debian's signed shim in front of it.
#
#   sudo bash make-uefi-netboot.sh [pxe-directory]
#
# Requires: grub-efi-amd64-bin  (and optionally shim-signed, grub-efi-amd64-signed)
set -euo pipefail

PXE="${1:-$(cd "$(dirname "$0")" && pwd)}"
log() { printf '\033[1;36m[uefi]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[uefi] ERROR\033[0m %s\n' "$*" >&2; exit 1; }

[ -d "$PXE" ] || die "no such directory: $PXE"
command -v grub-mknetdir >/dev/null || die "install grub-efi-amd64-bin first"

# --- unsigned path: works everywhere, needs Secure Boot off ------------------
log "generating grub netboot tree in $PXE/grub"
grub-mknetdir --net-directory="$PXE" --subdir=grub -d /usr/lib/grub/x86_64-efi >/dev/null
log "  -> DHCP option 67 for UEFI clients: grub/x86_64-efi/core.efi"

# --- signed path: keeps Secure Boot enabled ---------------------------------
SHIM=/usr/lib/shim/shimx64.efi.signed
GRUBNET=/usr/lib/grub/x86_64-efi-signed/grubnetx64.efi.signed
if [ -f "$SHIM" ] && [ -f "$GRUBNET" ]; then
    cp "$SHIM"    "$PXE/bootx64.efi"
    cp "$GRUBNET" "$PXE/grubx64.efi"
    [ -f /usr/lib/shim/mmx64.efi.signed ] && cp /usr/lib/shim/mmx64.efi.signed "$PXE/mmx64.efi"
    # The signed netboot GRUB looks for its configuration at <tftp-root>/grub/grub.cfg,
    # which is exactly where the image build put it.
    log "signed chain installed"
    log "  -> DHCP option 67 for Secure Boot UEFI clients: bootx64.efi"
else
    log "shim-signed / grub-efi-amd64-signed not installed - Secure Boot clients"
    log "  will need Secure Boot disabled, or install those two packages and re-run"
fi

# BIOS clients need pxelinux plus its modules in the TFTP root.
for f in pxelinux.0 ldlinux.c32 libcom32.c32 libutil.c32 menu.c32 vesamenu.c32; do
    [ -f "$PXE/$f" ] && continue
    for d in /usr/lib/PXELINUX /usr/lib/syslinux/modules/bios; do
        [ -f "$d/$f" ] && cp "$d/$f" "$PXE/" && break
    done
done
log "  -> DHCP option 67 for BIOS clients: pxelinux.0"

log "done"
