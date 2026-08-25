#!/bin/sh
set -eu

usage() {
    cat >&2 <<'EOF'
usage: deploy.sh <debian-lan-ip-or-hostname> [http-port] [pxe-directory]

Example:
  sudo ./deploy/docker-pxe/deploy.sh 192.168.1.20 8080 /srv/thinclient/pxe
EOF
    exit 2
}

die() {
    printf 'deploy.sh: %s\n' "$*" >&2
    exit 1
}

[ "$#" -ge 1 ] && [ "$#" -le 3 ] || usage

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/../.." && pwd)
HTTP_HOST=$1
HTTP_PORT=${2:-8080}
PXE_ROOT=${3:-"$REPO/out/pxe"}
USE_PREBUILT=0
if [ -n "${PXE_IMAGE:-}" ]; then
    USE_PREBUILT=1
else
    PXE_IMAGE=thinclient-pxe-server:1.4.1
fi

case "$HTTP_HOST" in
    *[!A-Za-z0-9._-]* | "")
        die "HTTP host must be an IPv4 address or DNS hostname without a port"
        ;;
esac
case "$HTTP_PORT" in
    *[!0-9]* | "")
        die "HTTP port must be a number"
        ;;
esac
[ "$HTTP_PORT" -ge 1 ] && [ "$HTTP_PORT" -le 65535 ] || die "HTTP port must be between 1 and 65535"
case "$PXE_IMAGE" in
    *[!A-Za-z0-9._/@:-]* | "")
        die "PXE_IMAGE contains unsupported characters"
        ;;
esac

command -v docker >/dev/null 2>&1 || die "Docker is not installed"
docker compose version >/dev/null 2>&1 || die "the Docker Compose plugin is not installed"

[ -d "$PXE_ROOT" ] || die "PXE directory does not exist: $PXE_ROOT"
PXE_ROOT=$(CDPATH= cd -- "$PXE_ROOT" && pwd)
[ -f "$PXE_ROOT/render-configs.sh" ] || die "not a generated ThinClient PXE tree: $PXE_ROOT"
if [ -d "$PXE_ROOT/thinclient/lite" ]; then
    [ -r "$PXE_ROOT/thinclient/lite/filesystem.squashfs" ] || die "Lite filesystem.squashfs is missing or unreadable"
    [ -r "$PXE_ROOT/thinclient/full/filesystem.squashfs" ] || die "Full filesystem.squashfs is missing or unreadable"
    [ -r "$PXE_ROOT/thinclient/lite/filesystem.squashfs.sha256" ] || die "Lite checksum sidecar is missing"
    [ -r "$PXE_ROOT/thinclient/full/filesystem.squashfs.sha256" ] || die "Full checksum sidecar is missing"
    for profile in lite full; do
        (cd "$PXE_ROOT/thinclient/$profile" && sha256sum -c filesystem.squashfs.sha256 >/dev/null) \
            || die "$profile squashfs checksum does not match its sidecar"
    done
else
    [ -r "$PXE_ROOT/thinclient/filesystem.squashfs" ] || die "filesystem.squashfs is missing or unreadable"
fi

bash "$PXE_ROOT/render-configs.sh" "$HTTP_HOST:$HTTP_PORT" --tftp-first

if find "$PXE_ROOT" -type f ! -perm -004 -print -quit | grep -q .; then
    die "some PXE files are not world-readable; run: chmod -R a+rX '$PXE_ROOT'"
fi

printf 'PXE_ROOT=%s\nPXE_LISTEN=%s\nHTTP_PORT=%s\nPXE_IMAGE=%s\n' \
    "$PXE_ROOT" "$HTTP_HOST" "$HTTP_PORT" "$PXE_IMAGE" > "$HERE/.env"

if [ "$USE_PREBUILT" -eq 1 ]; then
    docker compose \
        --project-directory "$HERE" \
        --env-file "$HERE/.env" \
        --file "$HERE/compose.yaml" \
        pull
    docker compose \
        --project-directory "$HERE" \
        --env-file "$HERE/.env" \
        --file "$HERE/compose.yaml" \
        up --detach --no-build
else
    docker compose \
        --project-directory "$HERE" \
        --env-file "$HERE/.env" \
        --file "$HERE/compose.yaml" \
        up --detach --build
fi

printf '\nThinClient PXE services started.\n'
printf '  TFTP next-server: %s\n' "$HTTP_HOST"
printf '  HTTP root:        http://%s:%s/\n' "$HTTP_HOST" "$HTTP_PORT"
printf '  Container image:  %s (%s)\n' "$PXE_IMAGE" \
    "$( [ "$USE_PREBUILT" -eq 1 ] && printf pulled || printf locally-built )"
printf '  Status:           docker compose -f %s/compose.yaml ps\n' "$HERE"
