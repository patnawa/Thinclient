#!/bin/bash
# =============================================================================
#  Runs INSIDE the chroot. Turns a minbase Debian into the thin client.
# =============================================================================
set -euo pipefail
log() { printf '\033[1;35m  [chroot]\033[0m %s\n' "$*"; }

export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C LANG=C

# --------------------------------------------------------------- slimming ---
# Drop documentation and translations before anything is unpacked, so we never
# pay for them. Worth roughly 120 MB on this package set.
cat > /etc/dpkg/dpkg.cfg.d/01_nodoc <<'EOF'
path-exclude /usr/share/doc/*
path-include /usr/share/doc/*/copyright
path-exclude /usr/share/man/*
path-exclude /usr/share/info/*
path-exclude /usr/share/groff/*
path-exclude /usr/share/lintian/*
path-exclude /usr/share/linda/*
path-exclude /usr/share/locale/*
path-include /usr/share/locale/en*
path-include /usr/share/locale/locale.alias
EOF

cat > /etc/apt/apt.conf.d/01lean <<'EOF'
APT::Install-Recommends "false";
APT::Install-Suggests "false";
Acquire::Languages "none";
EOF

# Nothing may start a daemon while we are in a chroot.
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
chmod +x /usr/sbin/policy-rc.d

# ------------------------------------------------------------------- apt -----
COMP_SPACED="${COMPONENTS//,/ }"
cat > /etc/apt/sources.list <<EOF
deb $MIRROR $SUITE $COMP_SPACED
deb $MIRROR ${SUITE}-updates $COMP_SPACED
deb $SECURITY_MIRROR ${SUITE}-security $COMP_SPACED
EOF

log "apt update"
apt-get update -qq

log "installing core package set"
mapfile -t PKGS < <(grep -vE '^\s*(#|$)' /tmp/packages.list)
apt-get install -y --no-install-recommends "${PKGS[@]}"

# ------------------------------------------------------- optional features ---
opt_install() { log "optional: $1"; shift; apt-get install -y --no-install-recommends "$@"; }

[ "${INCLUDE_VNC:-0}"        = "1" ] && opt_install "VNC viewer" \
    tigervnc-viewer
[ "${INCLUDE_PRINTING:-0}"   = "1" ] && opt_install "printing (CUPS)" \
    cups-daemon cups-client cups-filters cups-browsed cups-ipp-utils \
    avahi-daemon libnss-mdns printer-driver-gutenprint
[ "${INCLUDE_SMARTCARD:-0}"  = "1" ] && opt_install "smartcard (PC/SC)" \
    pcscd libccid opensc
[ "${INCLUDE_USB_REDIR:-0}"  = "1" ] && opt_install "USB redirection" \
    libusb-1.0-0 usbutils
[ "${INCLUDE_WIFI:-0}"       = "1" ] && opt_install "wifi" \
    wpasupplicant iw wireless-regdb
[ "${INCLUDE_WIFI_FIRMWARE:-0}" = "1" ] && opt_install "wifi firmware blobs" \
    firmware-iwlwifi firmware-atheros firmware-brcm80211 firmware-mediatek
[ "${INCLUDE_SSH_SERVER:-0}" = "1" ] && opt_install "key-only remote support" \
    openssh-server
[ "${INCLUDE_ADMIN_TOOLS:-0}" = "1" ] && opt_install "admin tools" \
    xterm openssh-client nano htop pciutils ethtool rsync

# Record which FreeRDP we shipped - the manager shows it on the status bar.
FREERDP_VER="$(dpkg-query -W -f='${Version}' freerdp3-x11 2>/dev/null || echo unknown)"
KERNEL_VER="$(dpkg-query -W -f='${Version}' linux-image-amd64 2>/dev/null || echo unknown)"

# --------------------------------------------------------------- identity ----
cat > /etc/os-release <<EOF
PRETTY_NAME="$DISTRO_NAME $DISTRO_VERSION"
NAME="$DISTRO_NAME"
VERSION_ID="$DISTRO_VERSION"
VERSION="$DISTRO_VERSION"
ID=thinclient
ID_LIKE=debian
HOME_URL="https://www.debian.org/"
EOF

mkdir -p /etc/thinclient
cat > /etc/thinclient/build-info <<EOF
name=$DISTRO_NAME
version=$DISTRO_VERSION
base=Debian $SUITE
built=$(date -u +%Y-%m-%dT%H:%M:%SZ)
freerdp=$FREERDP_VER
kernel=$KERNEL_VER
EOF

echo "thinclient" > /etc/hostname
cat > /etc/hosts <<'EOF'
127.0.0.1   localhost
127.0.1.1   thinclient
::1         localhost ip6-localhost ip6-loopback
ff02::1     ip6-allnodes
ff02::2     ip6-allrouters
EOF

# ------------------------------------------------------------ localisation ---
sed -i 's/^# *\(en_US.UTF-8 UTF-8\)/\1/' /etc/locale.gen
locale-gen >/dev/null
echo "LANG=en_US.UTF-8" > /etc/default/locale
ln -sf "/usr/share/zoneinfo/${DEFAULT_TIMEZONE}" /etc/localtime
echo "${DEFAULT_TIMEZONE}" > /etc/timezone

cat > /etc/default/keyboard <<EOF
XKBMODEL="pc105"
XKBLAYOUT="${DEFAULT_KEYMAP}"
XKBVARIANT=""
XKBOPTIONS=""
BACKSPACE="guess"
EOF

# ----------------------------------------------------------------- users -----
# Unprivileged kiosk account. No password: the box logs itself in and the only
# thing it can reach is the connection manager.
if ! id thin >/dev/null 2>&1; then
  useradd -m -s /bin/bash -c "Thin Client" thin
  passwd -d thin
fi
if [ "${INCLUDE_SSH_SERVER:-0}" = "1" ]; then
  if ! id support >/dev/null 2>&1; then
    useradd -m -s /bin/bash -c "ThinClient Remote Support" support
  fi
  # Keep the account unlocked for public-key SSH while making its local/SSH
  # password unknowable. Password authentication is also disabled in sshd.
  SUPPORT_RANDOM="$(openssl rand -hex 32)"
  usermod -p "$(openssl passwd -6 "$SUPPORT_RANDOM")" support
  unset SUPPORT_RANDOM
fi
# systemd-journal and adm let the Diagnostics page read the boot log without
# giving the kiosk user any other privilege.
for g in video audio input plugdev netdev lp render dialout systemd-journal adm; do
  getent group "$g" >/dev/null && usermod -aG "$g" thin || true
done
if id support >/dev/null 2>&1; then
  for g in netdev systemd-journal adm; do
    getent group "$g" >/dev/null && usermod -aG "$g" support || true
  done
fi
# Root has no password and no shell access from the console by default.
passwd -l root

# Let the kiosk user drive the few privileged actions the UI exposes.
install -d -m 0755 /etc/sudoers.d
cat > /etc/sudoers.d/thinclient <<'EOF'
thin ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff, \
    /usr/local/sbin/tc-apply-config, /usr/local/sbin/tc-save-config, \
    /usr/local/sbin/tc-fetch-config, /usr/sbin/pcscd, /usr/bin/nmcli, \
    /usr/local/sbin/tc-install
EOF
chmod 0440 /etc/sudoers.d/thinclient

# --------------------------------------------------------------- X server ----
# Appliance, not a workstation: let the server take the console it needs even
# when only the vesa/fbdev fallback drivers are available.
cat > /etc/X11/Xwrapper.config <<'EOF'
allowed_users=anybody
needs_root_rights=auto
EOF

# ------------------------------------------------------------- initramfs -----
sed -i 's/^MODULES=.*/MODULES=most/'   /etc/initramfs-tools/initramfs.conf
sed -i 's/^COMPRESS=.*/COMPRESS=zstd/' /etc/initramfs-tools/initramfs.conf
grep -q '^COMPRESS=' /etc/initramfs-tools/initramfs.conf || echo 'COMPRESS=zstd' >> /etc/initramfs-tools/initramfs.conf

# --------------------------------------------------------------- services ----
# Only stock packages here. Our own units are enabled by chroot-finalize.sh,
# which runs after the overlay has been laid down.
log "configuring services"
systemctl enable NetworkManager.service      >/dev/null 2>&1 || true
systemctl enable systemd-timesyncd.service   >/dev/null 2>&1 || true
[ "${INCLUDE_SMARTCARD:-0}" = "1" ] && systemctl enable pcscd.socket >/dev/null 2>&1 || true
if [ "${INCLUDE_PRINTING:-0}" = "1" ]; then
  systemctl enable cups.service cups-browsed.service avahi-daemon.service >/dev/null 2>&1 || true
fi
  if [ "${INCLUDE_SSH_SERVER:-0}" = "1" ]; then
    # A unit condition supplied by the overlay keeps the daemon closed until a
    # valid public key is found on TCCONF at boot.
    systemctl unmask ssh.service >/dev/null 2>&1 || true
    systemctl enable ssh.service >/dev/null 2>&1 || true
    systemctl disable ssh.socket >/dev/null 2>&1 || true
    systemctl mask ssh.socket >/dev/null 2>&1 || true
    # Debian's generated Wants= link would otherwise create throw-away host
    # keys even when ssh.service's support-key condition is false. The runtime
    # helper owns key generation and restores the same identity from TCCONF.
    systemctl mask sshd-keygen.service >/dev/null 2>&1 || true
  else
    # Cached rootfs builds must honour a later opt-out too: never leave a
    # previously installed daemon or socket enabled in an SSH-free image.
    systemctl disable ssh.service ssh.socket >/dev/null 2>&1 || true
    systemctl mask ssh.service ssh.socket sshd-keygen.service >/dev/null 2>&1 || true
  fi

# Audio belongs to the user session, so enable it for every user by default.
systemctl --global enable pipewire.socket pipewire-pulse.socket >/dev/null 2>&1 || true
systemctl --global enable wireplumber.service >/dev/null 2>&1 || true

# Boot time: nothing here waits on the network or scrubs disks.
#
# console-setup/keyboard-setup only style the text consoles, which this image
# boots straight past; X takes its layout from setxkbmap in tc-session. ldconfig
# rebuilds a cache that is already correct and can never change on a read-only
# image. Together these were costing over two seconds of every boot.
for u in apt-daily.timer apt-daily-upgrade.timer e2scrub_all.timer \
         fstrim.timer man-db.timer dpkg-db-backup.timer \
         NetworkManager-wait-online.service systemd-networkd-wait-online.service \
         console-setup.service keyboard-setup.service setvtrgb.service \
         ldconfig.service; do
  systemctl mask "$u" >/dev/null 2>&1 || true
done
# X owns vt7; tty1 stays a real console for on-site support.
systemctl set-default multi-user.target >/dev/null 2>&1 || true

# NetworkManager should manage every interface it finds, including a fresh
# netboot NIC that came up in the initramfs.
mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/10-thinclient.conf <<'EOF'
[main]
plugins=keyfile
dhcp=internal
[device]
wifi.scan-rand-mac-address=no
[connection]
ipv6.ip6-privacy=0
EOF

# systemd-timesyncd: correct clock is a hard requirement for NLA/Kerberos.
mkdir -p /etc/systemd/timesyncd.conf.d
cat > /etc/systemd/timesyncd.conf.d/10-thinclient.conf <<EOF
[Time]
NTP=${DEFAULT_NTP:-pool.ntp.org}
FallbackNTP=pool.ntp.org time.windows.com
EOF

# ------------------------------------------------------------ boot cosmetics -
cat > /etc/issue <<EOF
$DISTRO_NAME $DISTRO_VERSION  \\n \\l

EOF
cp /etc/issue /etc/issue.net

# The initramfs, our own units and the final clean-up all happen in
# chroot-finalize.sh, once the overlay is in place.
log "base ready - FreeRDP $FREERDP_VER, kernel $KERNEL_VER"
