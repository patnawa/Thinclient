#!/bin/bash
# ThinClient image build configuration.
# Every value can be overridden from the environment, e.g.:
#   DEFAULT_SERVER=10.0.0.5 bash build/build.sh

# ---------------------------------------------------------------- identity ---
DISTRO_NAME="${DISTRO_NAME:-ThinClient}"
DISTRO_VERSION="${DISTRO_VERSION:-1.4}"
IMAGE_NAME="${IMAGE_NAME:-thinclient-amd64}"

# ------------------------------------------------------------------- base ----
SUITE="${SUITE:-trixie}"                       # Debian 13 (LTS-ish, stable)
ARCH="${ARCH:-amd64}"
MIRROR="${MIRROR:-http://deb.debian.org/debian}"
SECURITY_MIRROR="${SECURITY_MIRROR:-http://security.debian.org/debian-security}"
COMPONENTS="${COMPONENTS:-main,contrib,non-free-firmware}"

# ------------------------------------------------------------------ build ----
# Build inside the Linux filesystem. Never point this at /mnt/c -- DrvFs cannot
# hold unix permissions, device nodes or hardlinks, and debootstrap will fail.
WORKDIR="${WORKDIR:-/opt/tcbuild}"

# Where finished artifacts are copied (Windows-visible path is fine here).
OUTDIR="${OUTDIR:-}"                           # default: <repo>/out

# ------------------------------------------------------------- feature set ---
INCLUDE_VNC="${INCLUDE_VNC:-1}"                # TigerVNC viewer for VNC connections (~1.4 MB)
INCLUDE_PRINTING="${INCLUDE_PRINTING:-1}"      # CUPS + filters for RDP printer redirection
INCLUDE_SMARTCARD="${INCLUDE_SMARTCARD:-1}"    # pcscd for /smartcard
INCLUDE_USB_REDIR="${INCLUDE_USB_REDIR:-1}"    # libusb + urbdrc for /usb
INCLUDE_WIFI="${INCLUDE_WIFI:-1}"              # wpa_supplicant + NM wifi support
INCLUDE_WIFI_FIRMWARE="${INCLUDE_WIFI_FIRMWARE:-1}"  # Intel/Qualcomm/Broadcom/MediaTek Wi-Fi firmware
INCLUDE_SOF_FIRMWARE="${INCLUDE_SOF_FIRMWARE:-1}" # audio DSP firmware for newer Intel systems
INCLUDE_AMD_MICROCODE="${INCLUDE_AMD_MICROCODE:-1}" # AMD CPU microcode (Intel remains in core)
INCLUDE_SECUREBOOT="${INCLUDE_SECUREBOOT:-1}"  # shim+grub signed -> boots with Secure Boot on
INCLUDE_ADMIN_TOOLS="${INCLUDE_ADMIN_TOOLS:-1}" # xterm, openssh-client, nano for support staff
INCLUDE_SSH_SERVER="${INCLUDE_SSH_SERVER:-1}"  # key-only support SSH; dormant until a public key exists
SUPPORT_AUTHORIZED_KEYS_FILE="${SUPPORT_AUTHORIZED_KEYS_FILE:-}" # optional public-key file copied to TCCONF
ENABLE_USB_CACHE="${ENABLE_USB_CACHE:-1}"      # reuse a verified squashfs from a removable TCCACHE USB
CACHE_LABEL="${CACHE_LABEL:-TCCACHE}"          # FAT32/exFAT/ext4 partition label used only for image caching
CACHE_PROFILE="${CACHE_PROFILE:-default}"      # isolated cache namespace (e.g. lite or full)

# "most" is safest for the full-driver image. The Lite PXE profile uses
# "list" and carries common desktop Ethernet drivers; every kernel module is
# still present in the squashfs and becomes available after Linux pivots root.
INITRAMFS_MODULES="${INITRAMFS_MODULES:-most}"
INITRAMFS_NET_MODULES="${INITRAMFS_NET_MODULES:-8139cp 8139too alx asix atl1c atl1e ax88179_178a bnx2 bnx2x cdc_ether cdc_ncm e1000 e1000e forcedeth hv_netvsc igb igc jme pcnet32 r8152 r8169 sis900 sky2 tg3 via-rhine via-velocity virtio_net vmxnet3}"

# A small writable FAT32 partition is appended to the hybrid image. When the
# image is written raw to USB, settings work immediately without repartitioning.
# Set to 0 only for optical-only images; FAT32 needs at least 64 MiB here.
TCCONF_SIZE_MB="${TCCONF_SIZE_MB:-64}"

# ------------------------------------------------------------ squashfs -------
SQUASH_COMP="${SQUASH_COMP:-zstd}"
SQUASH_LEVEL="${SQUASH_LEVEL:-19}"
SQUASH_BLOCK="${SQUASH_BLOCK:-1M}"

# ---------------------------------------------------------- default config ---
# Baked into the image as the factory default. Can be overridden at runtime by
# a TCCONF partition, a tc.config=<url> kernel argument, or DHCP option 224.
DEFAULT_SERVER="${DEFAULT_SERVER:-192.168.1.10}"
DEFAULT_SERVER_NAME="${DEFAULT_SERVER_NAME:-Windows Server 2025}"
DEFAULT_DOMAIN="${DEFAULT_DOMAIN:-}"
DEFAULT_TIMEZONE="${DEFAULT_TIMEZONE:-Asia/Bangkok}"
DEFAULT_KEYMAP="${DEFAULT_KEYMAP:-us}"
DEFAULT_NTP="${DEFAULT_NTP:-pool.ntp.org}"

# ------------------------------------------------------------ boot params ----
# 'toram' makes the client fully independent of the USB stick after boot and is
# what keeps a PXE client running when the server goes away.
KERNEL_CMDLINE="${KERNEL_CMDLINE:-boot=live components quiet loglevel=3 union=overlay}"

# --------------------------------------------------------- local overrides ---
# Site-specific values - your real server address, your time zone - belong here
# rather than in the file above, which is version controlled. This file is
# deliberately untracked, so a checkout never carries one site's addresses into
# another's, and nothing internal ends up published.
#
#   build/config.local.sh:
#       DEFAULT_SERVER=192.168.1.50
#       DEFAULT_DOMAIN=CORP
#
if [ -f "${BASH_SOURCE%/*}/config.local.sh" ]; then
    # shellcheck source=/dev/null
    . "${BASH_SOURCE%/*}/config.local.sh"
fi
