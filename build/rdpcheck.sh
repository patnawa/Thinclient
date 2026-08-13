#!/bin/bash
# Validate that every FreeRDP option this image generates is actually understood
# by the FreeRDP build that shipped in it. Catches option renames between
# FreeRDP versions before they reach a client.
#
#   sudo bash build/rdpcheck.sh
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
source "$REPO/build/config.sh"
ROOTFS="$WORKDIR/rootfs"
[ -d "$ROOTFS" ] || { echo "no rootfs at $ROOTFS - run build.sh first"; exit 1; }

CHROOT_RUN() { chroot "$ROOTFS" /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin \
                 HOME=/root LC_ALL=C "$@"; }

echo "=== FreeRDP version ==="
CHROOT_RUN xfreerdp3 --version 2>&1 | head -2

echo
echo "=== option support ==="
HELP=$(CHROOT_RUN xfreerdp3 --help 2>&1)
fail=0
check() {
    if printf '%s' "$HELP" | grep -q -- "$1"; then
        printf '  ok     %s\n' "$2"
    else
        printf '  MISSING %s  (looked for "%s")\n' "$2" "$1"; fail=1
    fi
}
check 'from-stdin'          'password via stdin (keeps it out of the process list)'
check '/sound'              'audio playback redirection'
check '/microphone'         'microphone redirection'
check '/smartcard'          'smart card redirection'
check '/printer'            'printer redirection'
check '/usb'                'raw USB redirection (urbdrc)'
check '/drive'              'drive redirection'
check '/multimon'           'multi-monitor'
check 'dynamic-resolution'  'dynamic resolution'
check '/gfx'                'RDP8 graphics pipeline'
check '/cert'               'certificate policy'
check 'auto-reconnect'      'automatic reconnect'
check '/audio-mode'         'audio mode'
check '/network'            'network autodetect'
check '/kbd'                'keyboard layout'
check '/timeout'            'connection timeout'
check '/log-level'          'log level'
check '/app'                'RemoteApp'
check '/g:'                 'RD Gateway'
check '/sec:'               'security protocol'

echo
echo "=== generated command line ==="
CMD=$(CHROOT_RUN /usr/local/bin/tc-connect main --print 2>&1)
echo "$CMD"

echo
echo "=== argument parsing ==="
# Feed FreeRDP exactly what the image would generate, retargeted at a closed
# port. A good command line gets as far as a connection error; a bad one dies
# in the option parser first.
set -- $CMD
ARGS=()
for a in "$@"; do
    case "$a" in
        /v:*) ARGS+=("/v:127.0.0.1:1") ;;
        */xfreerdp*) BIN="$a" ;;
        *) ARGS+=("$a") ;;
    esac
done
ARGS+=("/u:probe" "/p:probe")

OUT=$(CHROOT_RUN timeout 25 "${BIN:-xfreerdp3}" "${ARGS[@]}" 2>&1)
if printf '%s' "$OUT" | grep -qiE 'parsing failed|could not identify|unknown option|invalid option|unrecognized'; then
    echo "$OUT" | grep -iE 'parsing failed|could not identify|unknown option|invalid option' | head -4
    echo "  RESULT: PARSE ERROR - an option is wrong"; fail=1
else
    echo "$OUT" | tail -3
    echo "  RESULT: every option accepted (the connection failure is expected)"
fi

echo
[ "$fail" -eq 0 ] && echo "RDP CHECK PASSED" || echo "RDP CHECK FAILED"
exit "$fail"
