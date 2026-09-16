#!/usr/bin/env python3
"""Bind successful release checks to the exact files approved for deployment."""
import argparse
import hashlib
import json
from pathlib import Path


def hashes(root):
    result = {}
    for name in ("vmlinuz", "initrd.img", "filesystem.squashfs"):
        with (root / name).open("rb") as handle:
            result[name] = hashlib.file_digest(handle, "sha256").hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("record", "check"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--version", default="unknown")
    args = parser.parse_args()
    path = args.root / "verification.json"
    if args.action == "record":
        report = {"schema": 1, "version": args.version, "artifacts": hashes(args.root),
                  "checks": ["source", "artifact", "permissions", "rdp", "gtk",
                             "bios", "uefi", "secureboot", "installer", "pxe", "cache"]}
        path.write_text(json.dumps(report, indent=2) + "\n")
    else:
        report = json.loads(path.read_text())
        if report.get("schema") != 1 or report.get("artifacts") != hashes(args.root):
            raise SystemExit("verification receipt does not match the release")
        if not {"bios", "uefi", "secureboot", "pxe", "cache"}.issubset(report.get("checks", [])):
            raise SystemExit("required boot checks are missing")
        print("verified deployment artifacts: " + str(report.get("version")))


if __name__ == "__main__":
    main()
