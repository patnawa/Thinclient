#!/usr/bin/env python3
"""Summarize collected local event files; never contacts a client or server."""
import argparse
import collections
import json
from pathlib import Path
import statistics


def summarize(paths):
    groups = collections.defaultdict(list)
    for path in paths:
        for line in path.read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("event") == "ui_ready":
                groups[(event.get("version", "unknown"), event.get("profile", "unknown"),
                        event.get("cache_state", "unknown"))].append(event)
    rows = []
    for (version, profile, cache), events in sorted(groups.items()):
        times = [event["uptime_seconds"] for event in events if isinstance(event.get("uptime_seconds"), (int, float))]
        memory = [event["ram_used_kib"] for event in events if isinstance(event.get("ram_used_kib"), (int, float))]
        rows.append({"version": version, "profile": profile, "cache": cache, "samples": len(events),
                     "boot_seconds_median": statistics.median(times) if times else None,
                     "boot_seconds_max": max(times) if times else None,
                     "ram_kib_max": max(memory) if memory else None})
    return {"schema": 1, "measurements": rows,
            "note": "UI readiness is not proof of successful remote authentication or physical hardware support."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events", type=Path, nargs="+")
    print(json.dumps(summarize(parser.parse_args().events), indent=2))
