#!/bin/bash
# Run the unit tests against the module as installed in the built image.
#
# Running them inside the rootfs rather than on the build host means they use
# the real tcconfig.py and the real FreeRDP binary that shipped, so a test can
# never pass against a module the client does not actually have.
#
#   sudo bash build/unittest.sh            # all tests
#   sudo bash build/unittest.sh TargetAddress    # one class or method
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
source "$REPO/build/config.sh"
ROOTFS="$WORKDIR/rootfs"
[ -d "$ROOTFS" ] || { echo "no rootfs at $ROOTFS - run build.sh first"; exit 1; }

# Sync the library under test from the overlay so a red-green cycle does not
# need a full image rebuild. build.sh copies the same files, so what runs here
# is what ships.
for f in "$REPO"/overlay/usr/local/lib/thinclient/*.py; do
    sed 's/\r$//' "$f" > "$ROOTFS/usr/local/lib/thinclient/$(basename "$f")"
done
# Several safety tests exercise Python entry points as well as the importable
# library. Keep those in sync for the same quick red-green cycle.
for f in "$REPO"/overlay/usr/local/bin/tc-connect \
         "$REPO"/overlay/usr/local/sbin/tc-apply-config \
         "$REPO"/overlay/usr/local/sbin/tc-install; do
    destination="$ROOTFS/${f#$REPO/overlay/}"
    sed 's/\r$//' "$f" > "$destination"
done

install -d "$ROOTFS/opt/tests"
# tests/ holds the tests; test/ holds admin tools that some of them cover.
for f in "$REPO"/tests/*.py "$REPO"/test/*.py; do
    [ -f "$f" ] || continue
    sed 's/\r$//' "$f" > "$ROOTFS/opt/tests/$(basename "$f")"
done

# The deployment server is intentionally host-side, but exercising it in the
# image test environment catches Python-version and stdlib differences too.
install -d "$ROOTFS/opt/tools"
sed 's/\r$//' "$REPO/tools/tc-config-server.py" \
    > "$ROOTFS/opt/tools/tc-config-server.py"

if [ "$#" -gt 0 ]; then
    chroot "$ROOTFS" /usr/bin/env -i PATH=/usr/bin:/bin HOME=/root LC_ALL=C \
        PYTHONDONTWRITEBYTECODE=1 \
        /usr/bin/python3 -m unittest discover -s /opt/tests -t /opt/tests -v -k "$1"
else
    chroot "$ROOTFS" /usr/bin/env -i PATH=/usr/bin:/bin HOME=/root LC_ALL=C \
        PYTHONDONTWRITEBYTECODE=1 \
        /usr/bin/python3 -m unittest discover -s /opt/tests -t /opt/tests -v
fi
status=$?

rm -rf "$ROOTFS/opt/tests" "$ROOTFS/opt/tools"
exit "$status"
