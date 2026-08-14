#!/bin/bash
# Backward-compatible name for the generic Lite PXE profile.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

export DISTRO_VERSION="${DISTRO_VERSION:-1.3-lite}"
export IMAGE_NAME="${IMAGE_NAME:-thinclient-lite-amd64}"
export WORKDIR="${WORKDIR:-/opt/tcbuild-dell-legacy}"
export OUTDIR="${OUTDIR:-$REPO/out/legacy-dell}"

exec bash "$HERE/build-lite-pxe.sh"
