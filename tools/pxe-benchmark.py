#!/usr/bin/env python3
"""Checksum-verified static-image load tests; loopback only unless opted in."""
import argparse
import concurrent.futures
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import shutil
import statistics
import subprocess
import time
import urllib.parse


def validate_url(url, allow_remote=False):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or
            parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment):
        raise ValueError("use a static HTTP(S) URL without credentials, query or fragment")
    try:
        local = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        local = parsed.hostname == "localhost"
    if not local and not allow_remote:
        raise ValueError("remote load tests require --allow-remote and an approved maintenance window")
    return url


def parse_clients(value):
    clients = [int(item) for item in value.split(",")]
    if not clients or any(count < 1 or count > 128 for count in clients):
        raise ValueError("client counts must be 1..128")
    return clients


def download(url, expected_hash, expected_bytes, timeout):
    started = time.monotonic()
    digest, size = hashlib.sha256(), 0
    # curl owns the overall deadline, including headers and stalled transfers.
    # Do not follow redirects: an approved loopback URL must stay loopback.
    # Ignore ~/.curlrc: user defaults must not enable redirects, extra outputs,
    # credentials or retries that defeat this tool's load-test boundaries.
    with subprocess.Popen(["curl", "--disable", "--fail", "--silent", "--show-error",
                           "--max-time", str(timeout), "--connect-timeout", "10",
                           "--noproxy", "*", "--output", "-", url],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        for chunk in iter(lambda: process.stdout.read(256 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
        process.stderr.read()  # curl emits only bounded diagnostic lines here.
        code = process.wait()
    return {"seconds": round(time.monotonic() - started, 6), "bytes": size,
            "verified": code == 0 and size == expected_bytes and digest.hexdigest() == expected_hash,
            "curl_exit": code}


def summarize(results, elapsed):
    durations = sorted(item["seconds"] for item in results)
    total = sum(item["bytes"] for item in results)
    return {"clients": len(results), "verified": sum(item["verified"] for item in results),
            "failed": sum(not item["verified"] for item in results),
            "elapsed_seconds": round(elapsed, 3), "bytes_received": total,
            "aggregate_mib_per_second": round(total / max(elapsed, 0.001) / 1024 ** 2, 2),
            "client_seconds_median": statistics.median(durations),
            "client_seconds_p95": durations[math.ceil(len(durations) * 0.95) - 1]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--artifact", type=Path, required=True, help="trusted local copy to verify against")
    parser.add_argument("--clients", default="1,10,50")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        validate_url(args.url, args.allow_remote)
        clients = parse_clients(args.clients)
        if not 1 <= args.timeout <= 1800:
            raise ValueError("timeout must be 1..1800 seconds")
        if not shutil.which("curl"):
            raise ValueError("curl is required")
        with args.artifact.open("rb") as handle:
            expected = hashlib.file_digest(handle, "sha256").hexdigest()
        size = args.artifact.stat().st_size
        if size == 0:
            raise ValueError("trusted artifact is empty")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    report = {"schema": 1, "artifact": args.artifact.name, "sha256": expected,
              "artifact_bytes": size, "runs": [],
              "note": "HTTP transfer benchmark, not boot success or physical LAN certification."}
    for count in clients:
        started = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
            results = list(pool.map(lambda _: download(args.url, expected, size, args.timeout), range(count)))
        row = summarize(results, time.monotonic() - started)
        report["runs"].append(row)
        print(json.dumps(row), flush=True)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    raise SystemExit(1 if any(row["failed"] for row in report["runs"]) else 0)


if __name__ == "__main__":
    main()
