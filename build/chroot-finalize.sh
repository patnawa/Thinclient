#!/bin/bash
# =============================================================================
#  Second chroot pass. Runs AFTER the overlay has been copied in, so that our
#  files win over package conffiles and our units actually exist to be enabled.
# =============================================================================
set -euo pipefail
log() { printf '\033[1;35m  [chroot]\033[0m %s\n' "$*"; }

export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C LANG=C

# --------------------------------------------------------------- services ----
log "enabling ThinClient services"
systemctl enable thinclient.service >/dev/null 2>&1 || {
  echo "FATAL: thinclient.service missing from the overlay" >&2; exit 1;
}
systemctl enable tc-config.service  >/dev/null 2>&1 || {
  echo "FATAL: tc-config.service missing from the overlay" >&2; exit 1;
}
# Conditioned on tc.cache=1; machines without the parameter pay nothing.
systemctl enable tc-cache-save.service >/dev/null 2>&1 || {
  echo "FATAL: tc-cache-save.service missing from the overlay" >&2; exit 1;
}
# Conditioned on tc.debug=1, so it costs a production boot nothing.
systemctl enable tc-diag.service >/dev/null 2>&1 || true
# Conditioned on tc.install.auto=1; it erases a disk, so it must never be able
# to start by accident.
systemctl enable tc-autoinstall.service >/dev/null 2>&1 || true

# X lives on vt7; nothing else may claim it.
systemctl mask getty@tty7.service >/dev/null 2>&1 || true

# --------------------------------------------------------------- sanity ------
log "checking the overlay"
for f in /usr/local/bin/tc-session /usr/local/bin/tc-connect \
         /usr/local/sbin/tc-fetch-config /usr/local/sbin/tc-save-config \
         /usr/local/sbin/tc-apply-config /usr/local/sbin/tc-automount \
         /usr/local/sbin/tc-cache-save /usr/local/sbin/tc-prepare-support \
         /usr/lib/live/boot/9991-thinclient-cache.sh \
         /etc/initramfs-tools/hooks/thinclient-cache \
         /usr/local/sbin/tc-install /usr/local/sbin/tc-diag \
         /usr/local/lib/thinclient/manager.py /usr/local/lib/thinclient/settings.py \
         /usr/local/lib/thinclient/networkdiag.py \
         /usr/local/lib/thinclient/rdpprobe.py \
         /usr/local/lib/thinclient/installer.py \
         /usr/local/lib/thinclient/tcconfig.py /etc/thinclient/config.json; do
  [ -e "$f" ] || { echo "FATAL: missing $f" >&2; exit 1; }
done

# Every passwordless helper and every root-consumed policy must be unreachable
# through a group/world-writable path. This catches mode leakage from DrvFS,
# where repository directories commonly appear as 0777 to a WSL build.
python3 - <<'PYEOF'
import os
import stat

paths = (
    "/usr/local/sbin/tc-save-config",
    "/usr/local/sbin/tc-apply-config",
    "/usr/local/sbin/tc-fetch-config",
    "/usr/local/sbin/tc-cache-save",
    "/usr/lib/live/boot/9991-thinclient-cache.sh",
    "/etc/initramfs-tools/hooks/thinclient-cache",
    "/usr/local/sbin/tc-install",
    "/etc/NetworkManager/dispatcher.d/50-thinclient",
    "/etc/ssh/sshd_config.d/10-thinclient-support.conf",
    "/etc/systemd/system/ssh.service.d/10-thinclient-support.conf",
)
for path in paths:
    if not os.path.exists(path):
        continue
    current = "/"
    for part in path.strip("/").split("/"):
        current = os.path.join(current, part)
        info = os.stat(current)
        if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise SystemExit(
                "FATAL: replaceable privileged path component: %s (%o uid=%d)"
                % (current, stat.S_IMODE(info.st_mode), info.st_uid)
            )
PYEOF

# The installer partitions disks and installs bootloaders; without these it
# would fail halfway through, having already wiped the target.
for t in sgdisk partprobe mkfs.vfat wipefs lsblk findmnt grub-install; do
  command -v "$t" >/dev/null \
    || { echo "FATAL: tc-install needs $t, which is not installed" >&2; exit 1; }
done
[ -d /usr/lib/grub/x86_64-efi ] \
  || { echo "FATAL: grub-efi-amd64-bin missing - UEFI install would fail" >&2; exit 1; }
[ -d /usr/lib/grub/i386-pc ] \
  || { echo "FATAL: grub-pc-bin missing - BIOS install would fail" >&2; exit 1; }
for f in /usr/local/bin/tc-session /usr/local/bin/tc-connect \
         /usr/local/sbin/tc-fetch-config /usr/local/sbin/tc-save-config \
         /usr/local/sbin/tc-apply-config /usr/local/sbin/tc-automount \
         /usr/local/sbin/tc-cache-save /usr/local/sbin/tc-prepare-support \
         /usr/lib/live/boot/9991-thinclient-cache.sh \
         /etc/initramfs-tools/hooks/thinclient-cache \
         /etc/NetworkManager/dispatcher.d/50-thinclient; do
  [ -x "$f" ] || { echo "FATAL: $f is not executable" >&2; exit 1; }
done

# Catch a syntax error now rather than on a customer's screen.
python3 -m py_compile /usr/local/lib/thinclient/tcconfig.py \
                      /usr/local/lib/thinclient/manager.py \
                      /usr/local/lib/thinclient/settings.py \
                      /usr/local/lib/thinclient/networkdiag.py \
                      /usr/local/lib/thinclient/rdpprobe.py \
                      /usr/local/sbin/tc-apply-config \
                      /usr/local/bin/tc-connect
python3 -c 'import json; json.load(open("/etc/thinclient/config.json"))'
for s in /usr/local/bin/tc-session /usr/local/sbin/tc-fetch-config \
         /usr/local/sbin/tc-save-config /usr/local/sbin/tc-automount \
         /usr/local/sbin/tc-cache-save /usr/local/sbin/tc-prepare-support \
         /usr/lib/live/boot/9991-thinclient-cache.sh \
         /etc/initramfs-tools/hooks/thinclient-cache \
         /etc/NetworkManager/dispatcher.d/50-thinclient; do
  sh -n "$s" || { echo "FATAL: shell syntax error in $s" >&2; exit 1; }
done
find / -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# Keep Debian's downloader intact under a private name. The later live-boot
# component wraps it with a checksum-verified USB cache lookup.
if grep -q '^do_httpmount_network ()' /usr/lib/live/boot/9990-mount-http.sh; then
  : # Idempotent when reusing a prepared build root.
elif grep -q '^do_httpmount ()' /usr/lib/live/boot/9990-mount-http.sh; then
  sed -i '0,/^do_httpmount ()/s//do_httpmount_network ()/' \
    /usr/lib/live/boot/9990-mount-http.sh
else
  echo "FATAL: Debian live-boot HTTP function was not found" >&2
  exit 1
fi

# The manager must be able to import GTK, or the client boots to a black screen.
python3 -c 'import gi; gi.require_version("Gtk","3.0"); from gi.repository import Gtk' \
  || { echo "FATAL: python3-gi/GTK3 not usable" >&2; exit 1; }

command -v xfreerdp3 >/dev/null || command -v xfreerdp >/dev/null \
  || { echo "FATAL: no FreeRDP client installed" >&2; exit 1; }

if [ "${INCLUDE_SSH_SERVER:-0}" = "1" ]; then
  command -v sshd >/dev/null \
    || { echo "FATAL: INCLUDE_SSH_SERVER=1 but sshd is missing" >&2; exit 1; }
  id support >/dev/null 2>&1 \
    || { echo "FATAL: remote-support account is missing" >&2; exit 1; }
  [ -f /etc/ssh/sshd_config.d/10-thinclient-support.conf ] \
    || { echo "FATAL: hardened support sshd configuration is missing" >&2; exit 1; }
  [ -f /etc/systemd/system/ssh.service.d/10-thinclient-support.conf ] \
    || { echo "FATAL: conditional ssh.service configuration is missing" >&2; exit 1; }
  [ "$(stat -c '%a:%u' /etc/ssh/sshd_config.d/10-thinclient-support.conf)" = "644:0" ] \
    || { echo "FATAL: sshd support policy must be 0644 and root-owned" >&2; exit 1; }
  [ "$(stat -c '%a:%u' /etc/systemd/system/ssh.service.d)" = "755:0" ] \
    || { echo "FATAL: ssh.service drop-in directory must be 0755 and root-owned" >&2; exit 1; }
  [ "$(stat -c '%a:%u' /etc/systemd/system/ssh.service.d/10-thinclient-support.conf)" = "644:0" ] \
    || { echo "FATAL: ssh.service support gate must be 0644 and root-owned" >&2; exit 1; }
  [ "$(systemctl is-enabled ssh.socket 2>/dev/null || true)" = "masked" ] \
    || { echo "FATAL: ssh.socket could expose port 22 without a support key" >&2; exit 1; }
  [ "$(systemctl is-enabled sshd-keygen.service 2>/dev/null || true)" = "masked" ] \
    || { echo "FATAL: sshd-keygen could create an unapproved boot identity" >&2; exit 1; }
  [ "$(systemctl is-enabled ssh.service 2>/dev/null || true)" = "enabled" ] \
    || { echo "FATAL: conditional ssh.service is not enabled" >&2; exit 1; }
  install -d -m 0755 /run/sshd
  ssh-keygen -A >/dev/null
  sshd -t || { echo "FATAL: hardened sshd configuration is invalid" >&2; exit 1; }
elif command -v sshd >/dev/null 2>&1; then
  [ "$(systemctl is-enabled ssh.service 2>/dev/null || true)" = "masked" ] \
    || { echo "FATAL: cached ssh.service is not masked in an SSH-disabled build" >&2; exit 1; }
  [ "$(systemctl is-enabled ssh.socket 2>/dev/null || true)" = "masked" ] \
    || { echo "FATAL: cached ssh.socket is not masked in an SSH-disabled build" >&2; exit 1; }
fi

# --------------------------------------------------- privilege plumbing ------
# Every privileged action in the UI goes through sudo. minbase does not ship it,
# and without it Restart, Shut Down, saving settings and nmcli all fail silently.
command -v sudo >/dev/null || { echo "FATAL: sudo is not installed" >&2; exit 1; }
visudo -c -q -f /etc/sudoers.d/thinclient \
  || { echo "FATAL: /etc/sudoers.d/thinclient is not valid" >&2; exit 1; }

# Confirm the kiosk user is actually granted the commands the UI invokes.
for cmd in "/usr/bin/systemctl reboot" "/usr/bin/systemctl poweroff" \
           "/usr/local/sbin/tc-save-config" "/usr/local/sbin/tc-apply-config" \
           "/usr/bin/nmcli"; do
  sudo -l -U thin 2>/dev/null | grep -qF -- "$cmd" \
    || { echo "FATAL: thin is not permitted to run: $cmd" >&2; exit 1; }
done

# The manager runs as thin and writes its state under /run/thinclient.
[ -f /etc/tmpfiles.d/thinclient.conf ] \
  || { echo "FATAL: /etc/tmpfiles.d/thinclient.conf missing - /run/thinclient" \
            "would be root-only and the UI could not save anything" >&2; exit 1; }
grep -q '^d /run/thinclient .* thin ' /etc/tmpfiles.d/thinclient.conf \
  || { echo "FATAL: tmpfiles rule does not grant /run/thinclient to thin" >&2; exit 1; }

id -nG thin | tr ' ' '\n' | grep -qx systemd-journal \
  || echo "WARNING: thin cannot read the journal - Diagnostics will be empty" >&2

# ------------------------------------------------------------ initramfs ------
log "regenerating initramfs"
KVER="$(basename "$(ls -1 /boot/vmlinuz-* | sort -V | tail -1)" | sed 's/^vmlinuz-//')"
update-initramfs -u -k "$KVER"

# ----------------------------------------------------------------- clean -----
log "cleaning up"
apt-get -y autoremove --purge >/dev/null 2>&1 || true
apt-get -y clean
rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*.deb \
       /usr/share/doc/* /usr/share/man/* /usr/share/info/* \
       /var/log/* /tmp/* /root/.bash_history /var/tmp/*
find /usr/share/locale -mindepth 1 -maxdepth 1 \
     ! -name 'en*' ! -name 'locale.alias' -exec rm -rf {} + 2>/dev/null || true
rm -f /usr/sbin/policy-rc.d
: > /etc/machine-id          # regenerated per boot, so every client is distinct
rm -f /etc/ssh/ssh_host_*    # never ship shared host keys

log "image finalised"
