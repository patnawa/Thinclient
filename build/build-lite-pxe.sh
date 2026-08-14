#!/bin/bash
# Build a smaller wired-PXE image for older business and desktop PCs.
# It retains RDP, audio, graphics, BIOS/UEFI/Secure Boot, the installer and a
# broad selection of Intel/Realtek/Broadcom/Atheros/Marvell wired NIC drivers.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

export DISTRO_VERSION="${DISTRO_VERSION:-1.4-lite}"
export IMAGE_NAME="${IMAGE_NAME:-thinclient-lite-amd64}"
export WORKDIR="${WORKDIR:-/opt/tcbuild-lite}"
export OUTDIR="${OUTDIR:-$REPO/out/lite}"

export INCLUDE_VNC="${INCLUDE_VNC:-0}"
export INCLUDE_PRINTING="${INCLUDE_PRINTING:-0}"
export INCLUDE_SMARTCARD="${INCLUDE_SMARTCARD:-0}"
export INCLUDE_USB_REDIR="${INCLUDE_USB_REDIR:-0}"
export INCLUDE_WIFI="${INCLUDE_WIFI:-0}"
export INCLUDE_WIFI_FIRMWARE="${INCLUDE_WIFI_FIRMWARE:-0}"
export INCLUDE_SOF_FIRMWARE="${INCLUDE_SOF_FIRMWARE:-0}"
export INCLUDE_AMD_MICROCODE="${INCLUDE_AMD_MICROCODE:-0}"
export INCLUDE_ADMIN_TOOLS="${INCLUDE_ADMIN_TOOLS:-0}"
export INCLUDE_SSH_SERVER="${INCLUDE_SSH_SERVER:-0}"
export INCLUDE_SECUREBOOT="${INCLUDE_SECUREBOOT:-1}"

# PXE configuration is central, so this profile does not need the extra 64 MiB
# writable TCCONF partition in its optional ISO artifact.
export TCCONF_SIZE_MB="${TCCONF_SIZE_MB:-0}"

# Only Ethernet drivers are needed before the HTTP squashfs arrives. The full
# kernel module tree remains available after Linux switches to the live root.
export INITRAMFS_MODULES="${INITRAMFS_MODULES:-list}"
export CACHE_PROFILE="${CACHE_PROFILE:-lite}"

exec bash "$HERE/build.sh"
