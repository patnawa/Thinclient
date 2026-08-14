#!/bin/bash
# Verify the published hybrid image contains the writable-settings partition
# promised by the USB workflow, and that it carries the baked configuration.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
source "$REPO/build/config.sh"
ISO="${ISO:-$REPO/out/${IMAGE_NAME}-${DISTRO_VERSION}.iso}"
EXPECTED_CONFIG="${EXPECTED_CONFIG:-${OUTDIR:-$REPO/out}/config.json}"

[ -f "$ISO" ] || { echo "missing $ISO - run build.sh first"; exit 1; }
command -v xorriso >/dev/null || { echo "missing xorriso"; exit 1; }

if [ "$TCCONF_SIZE_MB" = "0" ]; then
    echo "TCCONF is disabled for this build (TCCONF_SIZE_MB=0)"
    exit 0
fi
[ -f "$EXPECTED_CONFIG" ] || {
    echo "missing $EXPECTED_CONFIG - use the config.json exported with this ISO"; exit 1;
}
command -v mtype >/dev/null || { echo "missing mtype (install mtools)"; exit 1; }

REPORT="$(xorriso -indev "$ISO" -report_system_area plain 2>/dev/null)"
read -r START SECTORS < <(
    awk '$1 == "GPT" && $2 == "start" && $3 == "and" && $4 == "size" \
             && $6 == "3" {print $7, $8; exit}' <<<"$REPORT"
)
[ -n "${START:-}" ] && [ -n "${SECTORS:-}" ] || {
    echo "ISO has no GPT partition 3 for TCCONF"; exit 1;
}

EXPECTED_SECTORS=$((TCCONF_SIZE_MB * 1024 * 1024 / 512))
[ "$SECTORS" -eq "$EXPECTED_SECTORS" ] || {
    echo "TCCONF has $SECTORS sectors; expected $EXPECTED_SECTORS"; exit 1;
}
grep -Eq '^MBR partition[[:space:]]*: *3 .* 0x0c ' <<<"$REPORT" || {
    echo "TCCONF is missing from the hybrid MBR as FAT32 LBA"; exit 1;
}

FAT="$ISO@@$((START * 512))"
mdir -i "$FAT" :: 2>/dev/null | grep -Eq 'Volume .* is TCCONF[[:space:]]*$' || {
    echo "partition 3 is not a FAT filesystem labelled TCCONF"; exit 1;
}
mdir -i "$FAT" ::/ca-certificates >/dev/null 2>&1 || {
    echo "TCCONF is missing ca-certificates/"; exit 1;
}
mdir -i "$FAT" ::/support >/dev/null 2>&1 || {
    echo "TCCONF is missing support/"; exit 1;
}
mtype -i "$FAT" ::/config.json 2>/dev/null | python3 -c '
import json, sys
embedded = json.load(sys.stdin)
with open(sys.argv[1], encoding="utf-8") as stream:
    expected = json.load(stream)
if embedded != expected:
    raise SystemExit("embedded config.json differs from out/config.json")
' "$EXPECTED_CONFIG"

echo "IMAGE CHECK PASSED"
echo "  partition 3: TCCONF, ${TCCONF_SIZE_MB} MiB FAT32"
echo "  embedded config.json matches the baked build configuration"
