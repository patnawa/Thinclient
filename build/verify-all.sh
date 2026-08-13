#!/bin/bash
# Run every automated check against the current build, in the order that fails
# cheapest first.
#
#   sudo bash build/verify-all.sh          # assumes out/ is current
#   sudo BUILD=1 bash build/verify-all.sh  # rebuild first
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
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
step "unit tests"         bash "$REPO/build/unittest.sh"
[ "${BUILD:-0}" = "1" ] && step "build" quiet bash "$REPO/build/build.sh"
step "permissions (as the kiosk user)" bash "$REPO/build/permcheck.sh"
step "FreeRDP options"    bash "$REPO/build/rdpcheck.sh"
step "connection manager renders" quiet bash "$REPO/build/uitest.sh" "$REPO/out/ui-preview.png"
step "settings dialog renders"    quiet bash "$REPO/build/uitest.sh" "$REPO/out/ui-settings.png" settings
step "real RDP session"   quiet bash "$REPO/build/rdpsession-test.sh"
step "boot: BIOS"         quiet bash "$REPO/build/boottest.sh" bios
step "boot: UEFI"         quiet bash "$REPO/build/boottest.sh" uefi
step "boot: Secure Boot"  quiet bash "$REPO/build/boottest.sh" secureboot
step "shut down button"   bash "$REPO/build/shutdowntest.sh" 5
step "install: BIOS"      quiet bash "$REPO/build/installtest.sh" bios
step "install: UEFI"      quiet bash "$REPO/build/installtest.sh" uefi
step "PXE: BIOS + central config" bash "$REPO/build/pxetest.sh" bios
step "PXE: UEFI + central config" bash "$REPO/build/pxetest.sh" uefi

printf '\n\033[1m===================== SUMMARY =====================\033[0m\n'
printf '  passed: %d\n  failed: %d\n' "$pass" "$fail"
for f in ${FAILED+"${FAILED[@]}"}; do printf '    - %s\n' "$f"; done
exit "$fail"
