#!/usr/bin/env python3
"""Validate a configuration before publishing it to a fleet."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "overlay/usr/local/lib/thinclient"))
from configfetch import validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--production", action="store_true")
    args = parser.parse_args()
    try:
        data = json.loads(args.config.read_text(encoding="utf-8"))
        errors = validate(data, args.production)
    except (OSError, ValueError):
        errors = ["could not read a valid JSON configuration"]
    for error in errors:
        print(error, file=sys.stderr)
    if not errors:
        print("configuration accepted")
    return bool(errors)


if __name__ == "__main__":
    sys.exit(main())
