#!/bin/sh
set -eu

PXE_ROOT="${PXE_ROOT:-/srv/pxe}"

die() {
    printf 'thinclient-pxe: %s\n' "$*" >&2
    exit 1
}

require_file() {
    [ -r "$PXE_ROOT/$1" ] || die "missing or unreadable PXE artifact: $PXE_ROOT/$1"
}

require_boot_profiles() {
    if [ -r "$PXE_ROOT/thinclient/lite/vmlinuz" ]; then
        for profile in lite full; do
            require_file "thinclient/$profile/vmlinuz"
            require_file "thinclient/$profile/initrd.img"
            if [ "${1:-}" != tftp ]; then
                require_file "thinclient/$profile/filesystem.squashfs"
                require_file "thinclient/$profile/filesystem.squashfs.sha256"
            fi
        done
    else
        require_file thinclient/vmlinuz
        require_file thinclient/initrd.img
        [ "${1:-}" = tftp ] || require_file thinclient/filesystem.squashfs
    fi
}

warn_test_address() {
    for config in pxelinux.cfg/default grub/grub.cfg boot.ipxe; do
        [ -r "$PXE_ROOT/$config" ] || continue
        if grep -Eq '\{\{HTTP\}\}|10\.0\.2\.2:8087' "$PXE_ROOT/$config"; then
            printf '%s\n' \
                "thinclient-pxe: WARNING: $config still contains a build/test HTTP address." \
                "thinclient-pxe: Run deploy/docker-pxe/deploy.sh with this host's LAN address." >&2
        fi
    done
}

case "${1:-}" in
    tftp)
        require_file pxelinux.0
        require_file pxelinux.cfg/default
        require_file bootx64.efi
        require_file grubx64.efi
        require_file grub/grub.cfg
        require_file grub/x86_64-efi/core.efi
        require_boot_profiles tftp
        warn_test_address
        exec /usr/sbin/in.tftpd \
            --foreground \
            --listen \
            --user tftp \
            --address "${TFTP_LISTEN:-0.0.0.0:69}" \
            --blocksize 1468 \
            --secure \
            --verbose \
            "$PXE_ROOT"
        ;;
    http)
        require_file config.json
        require_boot_profiles http
        warn_test_address
        exec python3 /usr/local/lib/thinclient/tc-config-server.py \
            --root "$PXE_ROOT" \
            --bind "${HTTP_BIND:-0.0.0.0}" \
            --port "${HTTP_PORT:-8080}" \
            --state-file "${STATUS_STATE_FILE:-/var/lib/thinclient/http-status.json}"
        ;;
    "")
        die "select a service: tftp or http"
        ;;
    *)
        exec "$@"
        ;;
esac
