#!/bin/bash
# Static checks over the whole tree. Run before a build; run again after edits.
#   wsl -d Debian -u root -- bash /mnt/c/.../build/check.sh
cd "$(dirname "$0")/.." || exit 1
fail=0
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

check_sh() {
  sed 's/\r$//' "$1" > "$tmp/s"
  if "$2" -n "$tmp/s" 2>"$tmp/err"; then
    echo "  ok    $1"
  else
    echo "  FAIL  $1"; sed 's/^/        /' "$tmp/err"; fail=1
  fi
}

echo "shell (bash):"
for f in build/*.sh pxe/*.sh; do [ -f "$f" ] || continue; check_sh "$f" bash; done

echo "shell (posix sh):"
for f in overlay/usr/local/bin/tc-session \
         overlay/usr/local/sbin/tc-fetch-config \
         overlay/usr/local/sbin/tc-save-config \
         overlay/usr/local/sbin/tc-automount \
         overlay/usr/local/sbin/tc-autoinstall \
         overlay/usr/local/sbin/tc-diag \
         overlay/etc/NetworkManager/dispatcher.d/50-thinclient; do
  [ -f "$f" ] || { echo "  MISS  $f"; fail=1; continue; }
  check_sh "$f" sh
done

echo "python:"
for f in overlay/usr/local/lib/thinclient/*.py \
         overlay/usr/local/sbin/tc-apply-config \
         overlay/usr/local/sbin/tc-install \
         overlay/usr/local/bin/tc-connect; do
  [ -f "$f" ] || { echo "  MISS  $f"; fail=1; continue; }
  name=$(basename "$f" | tr -c 'A-Za-z0-9' '_')
  sed 's/\r$//' "$f" > "$tmp/$name.py"
  if python3 -m py_compile "$tmp/$name.py" 2>"$tmp/err"; then
    echo "  ok    $f"
  else
    echo "  FAIL  $f"; sed 's/^/        /' "$tmp/err"; fail=1
  fi
done

echo "data files:"
for f in overlay/etc/thinclient/config.json; do
  if python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$f" 2>"$tmp/err"; then
    echo "  ok    $f"
  else
    echo "  FAIL  $f"; sed 's/^/        /' "$tmp/err"; fail=1
  fi
done
for f in overlay/etc/xdg/openbox/menu.xml; do
  if python3 -c "import xml.etree.ElementTree as t,sys; t.parse(sys.argv[1])" "$f" 2>"$tmp/err"; then
    echo "  ok    $f"
  else
    echo "  FAIL  $f"; sed 's/^/        /' "$tmp/err"; fail=1
  fi
done

echo "systemd units:"
for f in overlay/etc/systemd/system/*.service; do
  if grep -q '^\[Service\]' "$f" && grep -q '^ExecStart=' "$f"; then
    echo "  ok    $f"
  else
    echo "  FAIL  $f (no [Service]/ExecStart)"; fail=1
  fi
done

[ "$fail" -eq 0 ] && echo "ALL CHECKS PASSED" || echo "CHECKS FAILED"
exit "$fail"
