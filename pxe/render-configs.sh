#!/bin/bash
# Point every generated boot configuration at a real server address.
#
#   bash render-configs.sh 192.168.1.5            # rewrite configs
#   bash render-configs.sh 192.168.1.5 --serve    # ...and start a test HTTP server
#
# The address is where the client will fetch filesystem.squashfs over HTTP. It
# does not have to be the same box as the TFTP server.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SERVER="${1:-}"
MODE="${2:-}"

# This script ships in two places: the repository's pxe/ directory, and the
# generated tree in out/pxe. Only the generated tree has boot files to rewrite,
# so if we were started from the source copy, work on the generated one instead.
# Getting this wrong leaves {{HTTP}} unsubstituted, and the client then tries to
# fetch its root filesystem from a host literally named "{{HTTP}}".
if [ -f "$HERE/pxelinux.cfg/default" ]; then
    PXE="$HERE"
elif [ -f "$HERE/../out/pxe/pxelinux.cfg/default" ]; then
    PXE="$(cd "$HERE/../out/pxe" && pwd)"
    echo "  (using the generated tree at $PXE)"
else
    echo "No PXE tree found. Run build/build.sh first." >&2
    exit 1
fi

if [ -z "$SERVER" ]; then
    echo "usage: bash render-configs.sh <server-ip-or-host[:port]> [--serve]" >&2
    exit 1
fi

changed=0
for f in "$PXE/pxelinux.cfg/default" "$PXE/grub/grub.cfg" "$PXE/boot.ipxe"; do
    [ -f "$f" ] || continue
    if grep -q '{{HTTP}}' "$f"; then
        sed -i "s|{{HTTP}}|$SERVER|g" "$f"
        echo "  rewritten  ${f#$PXE/}"
        changed=$((changed + 1))
    else
        # Already rendered once: retarget it instead of leaving a stale address.
        sed -i -E "s|(http://)[^/[:space:]]+(/thinclient)|\1$SERVER\2|g; \
                   s|\(http,[^)]+\)|($SERVER)|g" "$f" 2>/dev/null || true
        echo "  updated    ${f#$PXE/}"
    fi
done
[ "$changed" -gt 0 ] || echo "  (configs had already been rendered - addresses refreshed)"

cat <<EOF

Serve these over TFTP + HTTP:

  TFTP root  : $PXE
  HTTP root  : $PXE          (so /thinclient/filesystem.squashfs resolves)

  DHCP option 66 (next-server) : $SERVER
  DHCP option 67 (boot file)   : pxelinux.0                    BIOS clients
                                 grub/x86_64-efi/core.efi      UEFI, Secure Boot off
                                 bootx64.efi                   UEFI, Secure Boot on

If the UEFI loaders are missing, run:  sudo bash $PXE/make-uefi-netboot.sh
EOF

if [ "$MODE" = "--serve" ]; then
    echo
    echo "serving $PXE on http://0.0.0.0:8080/ - Ctrl+C to stop"
    cd "$PXE"
    exec python3 -m http.server 8080
fi
