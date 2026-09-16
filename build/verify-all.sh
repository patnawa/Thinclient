#!/bin/bash
# Run every automated check against the current build, in the order that fails
# cheapest first.
#
#   sudo bash build/verify-all.sh          # assumes out/ is current
#   sudo BUILD=1 bash build/verify-all.sh  # rebuild first
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
source "$REPO/build/config.sh"
export DISTRO_VERSION IMAGE_NAME TCCONF_SIZE_MB CACHE_PROFILE ENABLE_USB_CACHE
export OUTDIR="${OUTDIR:-$REPO/out}"
export ISO="${ISO:-$OUTDIR/${IMAGE_NAME}-${DISTRO_VERSION}.iso}"
export PXE="${PXE:-$OUTDIR/pxe}"
export CONFIG_SOURCE="${CONFIG_SOURCE:-$OUTDIR/config.json}"
export VERIFICATION_OUT="$OUTDIR/verification"
mkdir -p "$VERIFICATION_OUT"
SCRATCH=""
cleanup() {
    [ -n "$SCRATCH" ] || return 0
    for relative in tmp/.X11-unix dev/pts dev/shm dev proc sys run; do
        if mountpoint -q "$SCRATCH/rootfs/$relative"; then
            umount "$SCRATCH/rootfs/$relative" || { echo "retaining mounted test directory: $SCRATCH" >&2; return 1; }
        fi
    done
    rm -rf -- "$SCRATCH"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
pass=0; fail=0
declare -a FAILED

step() {
    local name="$1"; shift
    printf '\n\033[1;36m=== %s ===\033[0m\n' "$name"
    if "$@"; then
        printf '\033[1;32m--- %s: PASS ---\033[0m\n' "$name"; pass=$((pass+1))
    else
        printf '\033[1;31m--- %s: FAIL ---\033[0m\n' "$name"; fail=$((fail+1)); FAILED+=("$name")
    fi
}

quiet() { "$@" >/tmp/verify-step.log 2>&1 || { tail -25 /tmp/verify-step.log; return 1; }; tail -4 /tmp/verify-step.log; }

step "static checks"      bash "$REPO/build/check.sh"
step "source unit tests"  bash "$REPO/build/unittest.sh"
[ "${BUILD:-0}" = "1" ] && step "build" quiet bash "$REPO/build/build.sh"
# Every image check receives a disposable extraction of the final squashfs.
# Nothing can overlay new source onto the image under test or change its ISO.
SQUASHFS="${SQUASHFS:-$PXE/thinclient/filesystem.squashfs}"
[ -r "$SQUASHFS" ] || { echo "missing release squashfs: $SQUASHFS" >&2; exit 1; }
SCRATCH="$(mktemp -d "${TC_TEST_TMPDIR:-/var/tmp}/thinclient-release-test.XXXXXX")" || exit 1
case ",$(findmnt -no OPTIONS -T "$SCRATCH")," in
    *,nosuid,*|*,nodev,*|*,noexec,*) echo 'image tests need a filesystem with suid, dev and exec enabled; set TC_TEST_TMPDIR' >&2; exit 1 ;;
esac
unsquashfs -no-progress -d "$SCRATCH/rootfs" "$SQUASHFS" >/dev/null || exit 1
export WORKDIR="$SCRATCH" TC_TEST_ROOTFS="$SCRATCH/rootfs" TC_CONFIG_LOCAL=/dev/null
export TC_UI_LIBRARY="$SCRATCH/rootfs/usr/local/lib/thinclient"
step "artifact unit tests" bash "$REPO/build/unittest.sh"
step "hybrid image layout" bash "$REPO/build/imagecheck.sh"
step "network adapter coverage" bash "$REPO/build/networkcheck.sh"
step "key-only remote support" bash "$REPO/build/supportcheck.sh"
step "permissions (as the kiosk user)" bash "$REPO/build/permcheck.sh"
step "FreeRDP options"    bash "$REPO/build/rdpcheck.sh"
if [ "$ENABLE_USB_CACHE" = 1 ]; then
    step "cache and initramfs structure" bash "$REPO/build/cachecheck.sh"
fi
step "connection manager renders" quiet bash "$REPO/build/uitest.sh" "$REPO/out/ui-preview.png"
step "settings dialog renders"    quiet bash "$REPO/build/uitest.sh" "$REPO/out/ui-settings.png" settings
step "Help dialog renders"        quiet bash "$REPO/build/uitest.sh" "$REPO/out/ui-about.png" about
step "changelog renders"          quiet bash "$REPO/build/uitest.sh" "$REPO/out/ui-changelog.png" changelog
step "network test renders"       quiet bash "$REPO/build/uitest.sh" "$REPO/out/ui-network-test.png" network-test
step "admin dialog renders"       quiet bash "$REPO/build/uitest.sh" "$REPO/out/ui-admin.png" admin
step "administrator setup renders" quiet bash "$REPO/build/uitest.sh" "$REPO/out/ui-setup.png" setup
step "progress dialog renders"    quiet bash "$REPO/build/uitest.sh" "$REPO/out/ui-progress.png" progress
step "error dialog renders"       quiet bash "$REPO/build/uitest.sh" "$REPO/out/ui-error.png" error
step "old monitor renders"        quiet env TC_UI_SCREEN=1024x768 bash "$REPO/build/uitest.sh" "$REPO/out/ui-1024x768.png"
step "real RDP session"   quiet bash "$REPO/build/rdpsession-test.sh"
step "boot: BIOS"         quiet env BOOTTEST_OUT="$VERIFICATION_OUT/bios" bash "$REPO/build/boottest.sh" bios
step "boot: UEFI"         quiet env BOOTTEST_OUT="$VERIFICATION_OUT/uefi" bash "$REPO/build/boottest.sh" uefi
step "boot: Secure Boot"  quiet env BOOTTEST_OUT="$VERIFICATION_OUT/secureboot" bash "$REPO/build/boottest.sh" secureboot
# Four tabs from the selected card reaches Power; the shutdown test then chooses
# Shut down inside the safe, Cancel-default power dialog.
step "shut down button"   env SHUTDOWNTEST_OUT="$VERIFICATION_OUT/shutdown" bash "$REPO/build/shutdowntest.sh" 4
step "install: BIOS"      quiet env INSTALLTEST_OUT="$VERIFICATION_OUT/install-bios" bash "$REPO/build/installtest.sh" bios
step "install: UEFI"      quiet env INSTALLTEST_OUT="$VERIFICATION_OUT/install-uefi" bash "$REPO/build/installtest.sh" uefi
step "PXE: BIOS + central config" env PXETEST_OUT="$VERIFICATION_OUT/pxe-bios" bash "$REPO/build/pxetest.sh" bios
step "PXE: UEFI + central config" env PXETEST_OUT="$VERIFICATION_OUT/pxe-uefi" bash "$REPO/build/pxetest.sh" uefi
if [ "$ENABLE_USB_CACHE" = 1 ]; then
    step "PXE: cold and cached boot" env CACHETEST_OUT="$VERIFICATION_OUT/cache" bash "$REPO/build/cacheboottest.sh"
fi

printf '\n\033[1m===================== SUMMARY =====================\033[0m\n'
printf '  passed: %d\n  failed: %d\n' "$pass" "$fail"
for f in ${FAILED+"${FAILED[@]}"}; do printf '    - %s\n' "$f"; done
if [ "$fail" -eq 0 ] && [ "$ENABLE_USB_CACHE" = 1 ]; then
    python3 "$REPO/tools/verification-receipt.py" record "$(dirname "$SQUASHFS")" \
        --version "$DISTRO_VERSION" || exit 1
fi
exit "$fail"
