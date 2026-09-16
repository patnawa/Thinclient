#!/usr/bin/env python3
"""Create or verify a signed manifest for one immutable PXE profile."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

FILES = ("vmlinuz", "initrd.img", "filesystem.squashfs")


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def manifest(root):
    return "".join("%s  %s\n" % (digest(root / name), name) for name in FILES)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "verify"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--key", type=Path, help="private key for create, public key for verify")
    parser.add_argument("--version", default="unknown")
    args = parser.parse_args()
    root = args.root.resolve()
    path = root / "manifest.sha256"
    signature = root / "manifest.sha256.sig"
    if args.action == "create":
        path.write_text(manifest(root), encoding="ascii")
        if args.key:
            subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(args.key),
                            "-out", str(signature), str(path)], check=True)
        report = {"version": args.version, "signed": bool(args.key), "artifacts": {
            name: {"bytes": (root / name).stat().st_size, "sha256": digest(root / name)}
            for name in FILES}}
        (root / "release.json").write_text(json.dumps(report, indent=2) + "\n")
    else:
        if args.key:
            subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(args.key),
                            "-signature", str(signature), str(path)], check=True)
        if path.read_text(encoding="ascii") != manifest(root):
            raise SystemExit("release files do not match the manifest")
        print("release manifest verified" + (" (signature checked)" if args.key else " (unsigned)"))


if __name__ == "__main__":
    main()
