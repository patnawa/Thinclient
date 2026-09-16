#!/bin/bash
# Source: bash build/unittest.sh [test-filter]
# Artifact: sudo bash build/unittest.sh --image out/pxe/thinclient/filesystem.squashfs
# The image lane never overlays source onto the code under test.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
if [ "${1:-}" != --image ] && [ -z "${TC_TEST_ROOTFS:-}" ]; then
    cd "$REPO"
    args=()
    [ "$#" -eq 0 ] || args=(-k "$1")
    exec env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v "${args[@]}"
fi

TEMP=""
cleanup() { [ -z "$TEMP" ] || rm -rf -- "$TEMP"; }
trap cleanup EXIT
if [ "${1:-}" = --image ]; then
    [ "$#" -ge 2 ] && [ -f "$2" ] || { echo 'supply an existing squashfs' >&2; exit 2; }
    TEMP="$(mktemp -d "${TC_TEST_TMPDIR:-/var/tmp}/thinclient-unittest.XXXXXX")"
    ROOTFS="$TEMP/rootfs"
    unsquashfs -no-progress -d "$ROOTFS" "$2" >/dev/null
    shift 2
else
    ROOTFS="$TC_TEST_ROOTFS"
fi
[ "$(id -u)" = 0 ] || { echo 'image tests require root' >&2; exit 2; }
[ -x "$ROOTFS/usr/bin/python3" ] || { echo 'invalid test rootfs' >&2; exit 2; }
# Only test fixtures/tools are staged. /opt/overlay resolves to the extracted
# artifact, so tests with checkout-relative paths still use the shipped files.
for directory in tests test tools build pxe; do
    install -d "$ROOTFS/opt/$directory"
    while IFS= read -r -d '' file; do
        case "$file" in *.py|*.sh) sed 's/\r$//' "$file" > "$ROOTFS/opt/$directory/$(basename "$file")" ;; esac
    done < <(find "$REPO/$directory" -maxdepth 1 -type f ! -name '*.local.sh' -print0)
done
ln -s / "$ROOTFS/opt/overlay"
ln -s /usr/share/thinclient/CHANGELOG.md "$ROOTFS/opt/CHANGELOG.md"
args=()
[ "$#" -eq 0 ] || args=(-k "$1")
chroot "$ROOTFS" /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root \
    LC_ALL=C PYTHONDONTWRITEBYTECODE=1 \
    /usr/bin/python3 -m unittest discover -s /opt/tests -t /opt/tests -v "${args[@]}"
