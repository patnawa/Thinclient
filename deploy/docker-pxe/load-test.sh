#!/bin/bash
# Exercise an isolated HTTP/TFTP service against a trusted local artifact.
set -euo pipefail

HOST="${1:-127.0.0.1}"
CLIENTS="${2:-50}"
HTTP_PORT="${3:-8080}"
TFTP_FILE="${4:-thinclient/lite/vmlinuz}"
EXPECTED_FILE="${5:?supply the trusted local copy of the requested artifact as argument 5}"
case "$HOST" in
    127.0.0.1|localhost|::1) ;;
    *) [ "${ALLOW_REMOTE_LOAD:-0}" = 1 ] || { echo 'remote load requires ALLOW_REMOTE_LOAD=1 and a maintenance window' >&2; exit 2; } ;;
esac
[ -s "$EXPECTED_FILE" ] || { echo 'trusted artifact missing or empty' >&2; exit 2; }
EXPECTED_SHA="$(sha256sum "$EXPECTED_FILE" | awk '{print $1}')"

case "$CLIENTS" in
    ''|*[!0-9]*) echo "clients must be a positive number" >&2; exit 2 ;;
esac
[ "$CLIENTS" -gt 0 ] && [ "$CLIENTS" -le 128 ] || { echo "clients must be 1..128" >&2; exit 2; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 2; }
command -v tftp >/dev/null || { echo "tftp-hpa is required" >&2; exit 2; }
command -v timeout >/dev/null || { echo 'timeout is required' >&2; exit 2; }

TESTDIR="$(mktemp -d)"
trap 'rm -rf "$TESTDIR"' EXIT
export HOST HTTP_PORT TFTP_FILE TESTDIR EXPECTED_SHA
URL_HOST="$HOST"
case "$HOST" in *:*) URL_HOST="[$HOST]" ;; esac
REMOTE_ARGS=()
[ "${ALLOW_REMOTE_LOAD:-0}" != 1 ] || REMOTE_ARGS=(--allow-remote)
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

printf 'HTTP: %s clients fetching %s\n' "$CLIENTS" "$TFTP_FILE"
python3 "$REPO/tools/pxe-benchmark.py" "http://$URL_HOST:$HTTP_PORT/$TFTP_FILE" \
    --artifact "$EXPECTED_FILE" --clients "$CLIENTS" "${REMOTE_ARGS[@]}"

printf 'TFTP: %s clients fetching %s\n' "$CLIENTS" "$TFTP_FILE"
start="$(date +%s%N)"
seq 1 "$CLIENTS" | xargs -P "$CLIENTS" -I{} sh -c \
    'timeout 60 tftp "$HOST" -m binary -c get "$TFTP_FILE" "$TESTDIR/kernel.{}" &&
     printf "%s  %s\n" "$EXPECTED_SHA" "$TESTDIR/kernel.{}" | sha256sum -c -'
end="$(date +%s%N)"

REFERENCE_SIZE="$(stat -c %s "$EXPECTED_FILE")"
GOOD="$(find "$TESTDIR" -type f -size "${REFERENCE_SIZE}c" | wc -l)"
HASHES="$(sha256sum "$TESTDIR"/* | awk '{print $1}' | sort -u | wc -l)"
printf '  complete files: %s/%s; unique hashes: %s; passed in %d ms\n' \
    "$GOOD" "$CLIENTS" "$HASHES" "$(( (end - start) / 1000000 ))"
[ "$GOOD" -eq "$CLIENTS" ]
[ "$HASHES" -eq 1 ]

echo "PXE CONCURRENCY TEST PASSED"
