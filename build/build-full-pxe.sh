#!/bin/bash
# Build the broad-compatibility PXE/ISO image, including Wi-Fi firmware,
# printing, smart cards, USB redirection, support tools and a full initramfs.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

export DISTRO_VERSION="${DISTRO_VERSION:-1.3-full}"
export IMAGE_NAME="${IMAGE_NAME:-thinclient-full-drivers-amd64}"
export WORKDIR="${WORKDIR:-/opt/tcbuild-full}"
export OUTDIR="${OUTDIR:-$REPO/out/full}"

export INCLUDE_VNC="${INCLUDE_VNC:-1}"
export INCLUDE_PRINTING="${INCLUDE_PRINTING:-1}"
export INCLUDE_SMARTCARD="${INCLUDE_SMARTCARD:-1}"
export INCLUDE_USB_REDIR="${INCLUDE_USB_REDIR:-1}"
export INCLUDE_WIFI="${INCLUDE_WIFI:-1}"
export INCLUDE_WIFI_FIRMWARE="${INCLUDE_WIFI_FIRMWARE:-1}"
export INCLUDE_SOF_FIRMWARE="${INCLUDE_SOF_FIRMWARE:-1}"
export INCLUDE_AMD_MICROCODE="${INCLUDE_AMD_MICROCODE:-1}"
export INCLUDE_ADMIN_TOOLS="${INCLUDE_ADMIN_TOOLS:-1}"
export INCLUDE_SSH_SERVER="${INCLUDE_SSH_SERVER:-1}"
export INCLUDE_SECUREBOOT="${INCLUDE_SECUREBOOT:-1}"
export INITRAMFS_MODULES="${INITRAMFS_MODULES:-most}"
export CACHE_PROFILE="${CACHE_PROFILE:-full}"

exec bash "$HERE/build.sh"
