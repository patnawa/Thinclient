#!/bin/bash
# Remove only builder-derived public keys before applying this build's inputs.
set -euo pipefail
root="$(realpath -e -- "${1:?supply a build rootfs}")"
settings="$(realpath -e -- "$root/etc/thinclient")"
[ "$root" != / ] || { echo 'refusing the host root' >&2; exit 2; }
case "$settings" in "$root"/*) ;; *) echo 'settings directory escapes rootfs' >&2; exit 2 ;; esac
rm -f -- "$settings/config.pub" "$settings/release.pub"
