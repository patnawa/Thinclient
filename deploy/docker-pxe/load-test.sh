#!/bin/bash
# Exercise the production HTTP and TFTP services with a parallel boot burst.
set -euo pipefail

HOST="${1:-127.0.0.1}"
CLIENTS="${2:-50}"
HTTP_PORT="${3:-8080}"
TFTP_FILE="${4:-thinclient/lite/vmlinuz}"

case "$CLIENTS" in
    ''|*[!0-9]*) echo "clients must be a positive number" >&2; exit 2 ;;
esac
[ "$CLIENTS" -gt 0 ] || { echo "clients must be a positive number" >&2; exit 2; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 2; }
command -v tftp >/dev/null || { echo "tftp-hpa is required" >&2; exit 2; }

TESTDIR="$(mktemp -d)"
trap 'rm -rf "$TESTDIR"' EXIT
export HOST HTTP_PORT TFTP_FILE TESTDIR

printf 'HTTP: %s clients fetching %s\n' "$CLIENTS" "$TFTP_FILE"
start="$(date +%s%N)"
seq 1 "$CLIENTS" | xargs -P "$CLIENTS" -I{} sh -c \
    'curl -fsS -o /dev/null "http://$HOST:$HTTP_PORT/$TFTP_FILE"'
end="$(date +%s%N)"
printf '  passed in %d ms\n' "$(( (end - start) / 1000000 ))"

printf 'TFTP: %s clients fetching %s\n' "$CLIENTS" "$TFTP_FILE"
start="$(date +%s%N)"
seq 1 "$CLIENTS" | xargs -P "$CLIENTS" -I{} sh -c \
    'tftp "$HOST" -m binary -c get "$TFTP_FILE" "$TESTDIR/kernel.{}"'
end="$(date +%s%N)"

REFERENCE_SIZE="$(stat -c %s "$TESTDIR/kernel.1")"
GOOD="$(find "$TESTDIR" -type f -size "${REFERENCE_SIZE}c" | wc -l)"
HASHES="$(sha256sum "$TESTDIR"/* | awk '{print $1}' | sort -u | wc -l)"
printf '  complete files: %s/%s; unique hashes: %s; passed in %d ms\n' \
    "$GOOD" "$CLIENTS" "$HASHES" "$(( (end - start) / 1000000 ))"
[ "$GOOD" -eq "$CLIENTS" ]
[ "$HASHES" -eq 1 ]

echo "PXE CONCURRENCY TEST PASSED"
