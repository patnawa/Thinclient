#!/bin/bash
# Package an already verified dual-profile release without site-local source
# overrides, private keys, Git history, build caches or session screenshots.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
[ "$#" = 3 ] || { echo 'usage: package-deployment.sh PXE_TREE RELEASE_PUBLIC_KEY OUTPUT.tar' >&2; exit 2; }
TREE="$(realpath "$1")"
PUBLIC_KEY="$(realpath "$2")"
openssl pkey -pubin -in "$PUBLIC_KEY" -noout >/dev/null
DESTINATION="$(realpath -m "$3")"
[ ! -e "$DESTINATION" ] || { echo 'refusing to overwrite an existing deployment bundle' >&2; exit 1; }
for profile in lite full; do
    python3 "$REPO/tools/verification-receipt.py" check "$TREE/thinclient/$profile"
    python3 "$REPO/tools/release-manifest.py" verify "$TREE/thinclient/$profile" --key "$PUBLIC_KEY"
done
scratch="$(mktemp -d /var/tmp/thinclient-package.XXXXXX)"
trap 'rm -rf -- "$scratch"' EXIT
mkdir "$scratch/source"
tar -C "$REPO" --exclude='config.local.sh' --exclude='*.local.md' \
    --exclude='.env' --exclude='certs' --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='*.pem' --exclude='*.key' --exclude='id_rsa*' --exclude='id_ed25519*' \
    -cf - .dockerignore .gitignore README.md CHANGELOG.md \
    build overlay pxe tools tests test deploy docs .github \
    | tar -C "$scratch/source" -xf -
# Normalize staged shell files for a Linux host when packaging from Windows.
find "$scratch/source" -type f -name '*.sh' -exec sed -i 's/\r$//' {} +
cp -a "$TREE" "$scratch/pxe"
install -m0644 "$PUBLIC_KEY" "$scratch/release-public.pem"
tar -C "$scratch" -cf "$DESTINATION" source pxe release-public.pem
sha256sum "$DESTINATION" > "$DESTINATION.sha256"
echo "verified deployment bundle: $DESTINATION"
