"""Bounded central configuration fetch, validation and credential-free status.

Policy is baked into /etc, never accepted from downloaded configuration.
Only the root-only runtime directory is used for temporary files and locks.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.parse

LIMIT = 1024 * 1024
DEADLINE = 20
POLICY = "/etc/thinclient/policy.json"


def read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def validate(data, production=False):
    errors = []
    if not isinstance(data, dict) or data.get("schema", 1) != 1:
        return ["configuration must be a schema 1 object"]
    device = data.get("device", {})
    connections = data.get("connections", [])
    if not isinstance(device, dict) or not isinstance(connections, list):
        return ["device must be an object and connections must be an array"]
    if any(not isinstance(item, dict) for item in connections):
        return ["each connection must be an object"]
    if production:
        password = device.get("admin_password", "")
        if not isinstance(password, str) or not password.startswith("pbkdf2-sha256$"):
            errors.append("set a PBKDF2 administrator password hash")
        for item in connections:
            if item.get("cert_policy", "strict") != "strict":
                errors.append("production connections must verify certificates")
            if item.get("password"):
                errors.append("remove stored session passwords")
            if item.get("security", "auto") == "rdp":
                errors.append("legacy RDP security is not a production policy")
    return sorted(set(errors))


def atomic_write(path, content, private, mode=0o644):
    descriptor, temporary = tempfile.mkstemp(dir=private, prefix="config-")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def download(url, destination, mac, deadline, runner=subprocess.run):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("configuration request timed out")
    # timeout bounds the entire curl process including retries and DNS. No
    # redirects/downgrades, unlimited response bodies or URL-bearing logs.
    result = runner([
        "curl", "--fail", "--silent", "--proto", "=http,https",
        "--connect-timeout", "4", "--max-time", str(min(8, remaining)),
        "--max-filesize", str(LIMIT), "--retry", "1", "--retry-delay", "1",
        "--retry-max-time", str(max(1, int(remaining - 1))),
        "-H", "X-ThinClient-MAC: " + mac, "--output", str(destination), url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=remaining)
    if result.returncode:
        raise ValueError("configuration server could not be reached or trusted")
    if destination.stat().st_size > LIMIT:
        raise ValueError("configuration response is too large")


def verify_signature(payload, signature, key, runner=subprocess.run):
    result = runner(["openssl", "dgst", "-sha256", "-verify", str(key),
                     "-signature", str(signature), str(payload)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
    if result.returncode:
        raise ValueError("configuration signature is invalid")


def fetch(url, run=Path("/run/thinclient"), policy=None, mac="unknown",
          downloader=download, verifier=verify_signature):
    policy = policy or {}
    private = run / ".root"
    remote = run / "remote-config.json"
    status_path = run / "config-status.json"
    status = read_json(status_path) or {}
    started = time.monotonic()
    url = policy.get("config_url") or url
    status.update(state="local", source="media" if (run / "media-config.json").is_file()
                  else "factory", error="", required=bool(policy.get("config_required")))
    try:
        if not url:
            if policy.get("config_required"):
                raise ValueError("central configuration is required")
            return status
        address = urllib.parse.urlsplit(url)
        if (address.scheme not in ("http", "https") or not address.hostname
                or address.username or address.password):
            raise ValueError("configuration URL is invalid")
        key = policy.get("config_public_key")
        if policy.get("require_https") and address.scheme != "https":
            raise ValueError("configuration policy requires HTTPS")
        with tempfile.TemporaryDirectory(prefix="fetch-", dir=private) as scratch:
            payload = Path(scratch) / "config.json"
            downloader(url, payload, mac, started + DEADLINE)
            if key:
                signature = Path(scratch) / "config.json.sig"
                signature_url = urllib.parse.urlunsplit(address._replace(path=address.path + ".sig"))
                downloader(signature_url, signature, mac, started + DEADLINE)
                verifier(payload, signature, key)
            raw = payload.read_bytes()
            data = json.loads(raw)
            errors = validate(data, policy.get("production", False))
            if errors:
                raise ValueError("; ".join(errors))
            atomic_write(remote, raw, private)
            status.update(state="current", source="central", error="",
                          version=str(data.get("version", "unversioned"))[:64],
                          sha256=hashlib.sha256(raw).hexdigest(),
                          last_success=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          authenticated=bool(key or address.scheme == "https"))
    except (ValueError, OSError, subprocess.SubprocessError):
        # Keep an already validated runtime copy on transient outages. A new
        # boot has no such copy and a required policy cannot silently open it.
        last_good = read_json(remote)
        valid_previous = last_good is not None and not validate(last_good, policy.get("production", False))
        status.update(state="stale" if valid_previous else "unavailable",
                      source="last-known-good" if valid_previous else status["source"],
                      error="Central configuration unavailable or rejected; contact your administrator.")
    finally:
        status["duration_seconds"] = round(time.monotonic() - started, 3)
        atomic_write(status_path, json.dumps(status, sort_keys=True).encode(), private)
    return status


def main():
    import fcntl
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="")
    args = parser.parse_args()
    private = Path("/run/thinclient/.root")
    info = private.lstat()
    if private.is_symlink() or info.st_uid != 0 or info.st_mode & 0o777 != 0o700:
        raise SystemExit("unsafe private configuration directory")
    policy = read_json(POLICY)
    if not isinstance(policy, dict):
        # A malformed existing policy must never silently disable enforcement.
        policy = {"config_required": True, "require_https": True} if Path(POLICY).exists() else {}
    mac = "unknown"
    for interface in sorted(Path("/sys/class/net").glob("*/address")):
        candidate = interface.read_text().strip()
        if candidate != "00:00:00:00:00:00":
            mac = candidate
            break
    with open(private / "fetch.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        fetch(args.url, policy=policy, mac=mac)


if __name__ == "__main__":
    main()
