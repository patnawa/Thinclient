#!/usr/bin/env python3
"""Measure VM launch to UI readiness, including firmware/network-loader time."""
import argparse
import json
from pathlib import Path
import time


def measurement(event, started, observed):
    elapsed = round(max(0, observed - started), 3)
    kernel = event.get("uptime_seconds")
    return {"schema": 1, "scope": "vm-launch-to-ui", "host_elapsed_seconds": elapsed,
            "kernel_to_ui_seconds": kernel, "version": event.get("version"),
            "profile": event.get("profile"), "cache_state": event.get("cache_state"),
            "config_state": event.get("config_state"), "ram_used_kib": event.get("ram_used_kib"),
            "note": "Includes VM startup and firmware; excludes remote desktop authentication. "
                    "Polling resolution is 0.1 seconds; not a physical power-on measurement."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ready", type=Path)
    parser.add_argument("--started", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=190)
    args = parser.parse_args()
    deadline = args.started + args.timeout
    while time.monotonic() < deadline:
        try:
            with args.ready.open() as handle:
                lines = handle.read(64 * 1024).splitlines()
            for line in lines:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("event") == "ui_ready":
                    report = measurement(event, args.started, time.monotonic())
                    temporary = args.output.with_suffix(".tmp")
                    temporary.write_text(json.dumps(report, indent=2) + "\n")
                    temporary.replace(args.output)
                    return
        except OSError:
            pass
        time.sleep(0.1)
    raise SystemExit("UI readiness was not observed within the VM timing window")


if __name__ == "__main__":
    main()
