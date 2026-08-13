#!/bin/bash
# Exercise the real key-only SSH provisioning path inside the built rootfs.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
source "$REPO/build/config.sh"
[ "$INCLUDE_SSH_SERVER" = "1" ] || {
    echo "SSH support is disabled for this build"; exit 0;
}
ROOTFS="$WORKDIR/rootfs"
[ -d "$ROOTFS" ] || { echo "no rootfs at $ROOTFS - run build.sh first"; exit 1; }

[ "$(stat -c '%a:%u' "$ROOTFS/etc/ssh/sshd_config.d/10-thinclient-support.conf")" = "644:0" ]
[ "$(stat -c '%a:%u' "$ROOTFS/etc/systemd/system/ssh.service.d")" = "755:0" ]
[ "$(stat -c '%a:%u' "$ROOTFS/etc/systemd/system/ssh.service.d/10-thinclient-support.conf")" = "644:0" ]

# Test the current source helper, not a potentially stale copy from an older
# build, while retaining the built image's users, sshd, and configuration.
sed 's/\r$//' "$REPO/overlay/usr/local/sbin/tc-prepare-support" \
  > "$ROOTFS/usr/local/sbin/tc-prepare-support"
chmod 0755 "$ROOTFS/usr/local/sbin/tc-prepare-support"

mounted=0
systemctl_bound=0
cleanup() {
    rm -f "$ROOTFS/etc/ssh"/ssh_host_ed25519_key* \
          "$ROOTFS/etc/ssh"/ssh_host_rsa_key* 2>/dev/null || true
    if [ "$systemctl_bound" -eq 1 ]; then
        umount "$ROOTFS/usr/bin/systemctl" 2>/dev/null || true
    fi
    if [ "$mounted" -eq 1 ]; then umount "$ROOTFS/run" 2>/dev/null || true; fi
}
trap cleanup EXIT
mountpoint -q "$ROOTFS/run" || { mount -t tmpfs tmpfs "$ROOTFS/run"; mounted=1; }

# Intercept only this chroot's absolute systemctl path. The privileged runtime
# helper itself has no environment-controlled command hook.
cat > "$ROOTFS/run/support-systemctl" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> /run/support-systemctl.log
EOF
chmod 0755 "$ROOTFS/run/support-systemctl"
mount --bind "$ROOTFS/run/support-systemctl" "$ROOTFS/usr/bin/systemctl"
systemctl_bound=1

chroot "$ROOTFS" /bin/sh -eu <<'EOF'
install -d -m 1775 -o root -g thin /run/thinclient
install -d -m 0755 /run/thinclient/media/support

# A labelled non-FAT filesystem can contain symlinks. It must not redirect the
# privileged host-key writer outside TCCONF.
mv /run/thinclient/media/support /run/thinclient/media/support-real
ln -s /etc /run/thinclient/media/support
/usr/local/sbin/tc-prepare-support 1
[ ! -e /run/thinclient-support/enabled ]
tail -1 /run/support-systemctl.log | grep -Fxq 'stop --no-block ssh.service'
rm /run/thinclient/media/support
mv /run/thinclient/media/support-real /run/thinclient/media/support

# Wrong-shaped input must leave the daemon gated off.
printf 'not a public key\n' > /run/thinclient/media/support/authorized_keys
/usr/local/sbin/tc-prepare-support 1
[ ! -e /run/thinclient-support/enabled ]

# A fresh public key enables support and creates a stable per-device identity.
ssh-keygen -q -N '' -t ed25519 -f /run/support-check >/dev/null
cp /run/support-check.pub /run/thinclient/media/support/authorized_keys
/usr/local/sbin/tc-prepare-support 1
[ -s /run/thinclient-support/enabled ]
tail -1 /run/support-systemctl.log | grep -Fxq 'start --no-block ssh.service'
[ -s /run/thinclient-support/user/authorized_keys ]
[ "$(stat -c '%a:%u:%g' /run/thinclient-support/user)" = "700:0:0" ]
[ "$(stat -c '%a:%u:%g' /run/thinclient-support/user/authorized_keys)" = "600:0:0" ]
[ -s /run/thinclient/media/support/host-keys/ssh_host_ed25519_key ]
[ -s /etc/ssh/ssh_host_ed25519_key ]
cmp /run/thinclient/media/support/host-keys/ssh_host_ed25519_key \
    /etc/ssh/ssh_host_ed25519_key

# A valid physical key is not sufficient when fetch rejected the mount.
/usr/local/sbin/tc-prepare-support 0
[ ! -e /run/thinclient-support/enabled ]
/usr/local/sbin/tc-prepare-support 1
[ -s /run/thinclient-support/enabled ]

# Even a valid but over-permissive persistent private key is rejected rather
# than imported by root.
chmod 0644 /run/thinclient/media/support/host-keys/ssh_host_ed25519_key
/usr/local/sbin/tc-prepare-support 1
[ ! -e /run/thinclient-support/enabled ]
chmod 0600 /run/thinclient/media/support/host-keys/ssh_host_ed25519_key
/usr/local/sbin/tc-prepare-support 1
[ -s /run/thinclient-support/enabled ]

install -d -m 0755 /run/sshd
settings="$(sshd -T -C user=support,host=thinclient,addr=127.0.0.1)"
for required in 'permitrootlogin no' 'passwordauthentication no' \
                'kbdinteractiveauthentication no' 'x11forwarding no' \
                'pubkeyauthentication yes' \
                'authenticationmethods publickey' 'allowusers support' \
                'disableforwarding yes' 'allowagentforwarding no' \
                'allowtcpforwarding no' 'allowstreamlocalforwarding no' \
                'permittunnel no' 'permituserenvironment no' \
                'authorizedkeysfile /run/thinclient-support/user/authorized_keys'; do
    printf '%s\n' "$settings" | grep -Fxq "$required" \
      || { echo "missing sshd hardening: $required"; exit 1; }
done

# Removing physical authorization closes the runtime gate on the next fetch.
rm -f /run/thinclient/media/support/authorized_keys
/usr/local/sbin/tc-prepare-support 1
[ ! -e /run/thinclient-support/enabled ]
[ ! -e /run/thinclient-support/user/authorized_keys ]
tail -1 /run/support-systemctl.log | grep -Fxq 'stop --no-block ssh.service'
! grep -q 'is-active\|^start ssh.service$\|^stop ssh.service$' \
    /run/support-systemctl.log
rm -f /run/support-check /run/support-check.pub /run/support-systemctl.log
EOF

echo "REMOTE SUPPORT CHECK PASSED"
echo "  invalid/missing keys keep SSH disabled"
echo "  valid keys provision stable host identity and hardened key-only access"
echo "  boot/reload service jobs are non-blocking and revocation-safe"
