"""Bounded, local-only lifecycle measurements without connection identity."""
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time

LOCK = threading.Lock()
EVENTS = {"ui_ready", "client_started", "session_ended", "session_failed"}
FIELDS = {"version", "profile", "elapsed_seconds", "exit_code", "cache_state", "config_state"}


def sample(event, fields, proc=Path("/proc")):
    if event not in EVENTS:
        raise ValueError("unknown event")
    result = {"schema": 1, "event": event, "timestamp": int(time.time())}
    result.update({key: value for key, value in fields.items() if key in FIELDS
                   and isinstance(value, (str, int, float))})
    try:
        result["uptime_seconds"] = float((proc / "uptime").read_text().split()[0])
        memory = dict(line.split(":", 1) for line in (proc / "meminfo").read_text().splitlines())
        result["ram_used_kib"] = int(memory["MemTotal"].split()[0]) - int(memory["MemAvailable"].split()[0])
    except (OSError, ValueError, KeyError):
        pass
    return result


def record(event, fields=None, path=Path("/run/thinclient/events.jsonl")):
    payload = sample(event, fields or {})
    with LOCK:
        temporary = None
        try:
            previous = []
            if path.is_file() and not path.is_symlink():
                previous = path.read_text()[-128 * 1024:].splitlines()[-127:]
            descriptor, temporary = tempfile.mkstemp(prefix="events-", dir=path.parent)
            with os.fdopen(descriptor, "w") as handle:
                handle.write("\n".join(previous + [json.dumps(payload, sort_keys=True)]) + "\n")
            os.replace(temporary, path)
        except OSError:
            pass
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
    # Only isolated VM tests attach this port; no physical serial port or
    # network endpoint receives these local measurements.
    port = "/dev/virtio-ports/org.thinclient.test"
    try:
        if event == "ui_ready" and stat.S_ISCHR(os.stat(port).st_mode):
            descriptor = os.open(port, os.O_WRONLY | os.O_NONBLOCK | os.O_NOCTTY)
            try:
                os.write(descriptor, (json.dumps(payload) + "\n").encode())
            finally:
                os.close(descriptor)
    except OSError:
        pass
    return False
