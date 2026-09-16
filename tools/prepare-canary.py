#!/usr/bin/env python3
"""Prepare an isolated PXE tree that selects a candidate for explicit MACs.

The existing default and both input trees are left untouched. No server is
contacted and no deployment is performed by this command.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import sys

MAC = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}\Z")


def candidate_menu(text):
    text = text.replace("/thinclient/", "/releases/canary/thinclient/")
    text = text.replace("KERNEL thinclient/", "KERNEL releases/canary/thinclient/")
    text = text.replace("initrd=thinclient/", "initrd=releases/canary/thinclient/")
    return text.replace("/config.json", "/releases/canary/config.json")


def prepare(stable, candidate, output, addresses):
    stable, candidate, output = stable.resolve(), candidate.resolve(), output.resolve()
    if output.exists() or any(output == root or output.is_relative_to(root) for root in (stable, candidate)):
        raise ValueError("output must be a new directory outside both input trees")
    addresses = sorted(set(address.lower().replace("-", ":") for address in addresses))
    if not addresses or any(not MAC.fullmatch(address) for address in addresses):
        raise ValueError("supply at least one complete client MAC")
    for root in (stable, candidate):
        for name in ("grub/grub.cfg", "pxelinux.cfg/default", "config.json"):
            if not (root / name).is_file():
                raise ValueError("input is not a complete PXE tree")
    shutil.copytree(stable, output)
    shutil.copytree(candidate, output / "releases/canary")
    bios = candidate_menu((candidate / "pxelinux.cfg/default").read_text())
    grub = candidate_menu((candidate / "grub/grub.cfg").read_text())
    (output / "grub/canary.cfg").write_text(grub)
    for address in addresses:
        (output / "pxelinux.cfg" / ("01-" + address.replace(":", "-"))).write_text(bios)
    choices = []
    for address in addresses:
        choices.append('if [ "$net_default_mac" = "%s" ]; then\n'
                       '    configfile /grub/canary.cfg\nfi\n' % address)
    default = output / "grub/grub.cfg"
    default.write_text("".join(choices) + default.read_text())
    (output / "canary.json").write_text(json.dumps({"schema": 1, "macs": addresses}, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stable", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--mac", action="append", required=True)
    args = parser.parse_args()
    prepare(args.stable, args.candidate, args.output, args.mac)
    print("canary tree prepared; default clients retain the stable release")


if __name__ == "__main__":
    main()
