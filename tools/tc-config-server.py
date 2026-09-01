#!/usr/bin/env python3
"""Central configuration server for ThinClient.

Serves the PXE tree and, more importantly, the one configuration file every
client pulls at boot. Point the fleet at this with DHCP option 224 (or
tc.config= on the kernel command line) and you can repoint every thin client in
the building by editing a single file.

    python3 tc-config-server.py --root out/pxe --port 8080

Per-device configuration works the way a Wyse unit's wnos.ini does: the client
sends its MAC in the X-ThinClient-MAC header, so if a file named

    config-aa-bb-cc-dd-ee-ff.json

exists it is served to that device, and everyone else gets config.json. Nothing
has to be generated per client and nothing has to know in advance which clients
exist.

The MAC header is a configuration-selection hint, not authentication. A client
can claim any MAC, so per-device files must not contain secrets that require an
access-control boundary.

Requests are logged with the MAC, which doubles as an inventory of what booted
and when.
"""

import argparse
import collections
import datetime
import html
import http.server
import ipaddress
import json
import os
import re
import socket
import socketserver
import sys
import threading
import time
import urllib.parse
import zoneinfo

CONFIG_NAME = "config.json"
MAC_RE = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}\Z", re.IGNORECASE)
PER_DEVICE_CONFIG_RE = re.compile(
    r"config-[0-9a-f]{2}(?:-[0-9a-f]{2}){5}\.json\Z"
)
STATUS_PATHS = frozenset(("/healthz", "/status", "/status/", "/status.json"))


def utc_timestamp(timestamp):
    """Format an epoch timestamp as an RFC 3339 UTC value."""
    return datetime.datetime.fromtimestamp(
        timestamp, datetime.timezone.utc
    ).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_status_timezone(name):
    """Resolve an IANA timezone name used only by the human status page."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("status timezone must be a non-empty IANA name")
    name = name.strip()
    if name in ("UTC", "Etc/UTC"):
        return datetime.timezone.utc
    try:
        return zoneinfo.ZoneInfo(name)
    except (ValueError, zoneinfo.ZoneInfoNotFoundError) as exc:
        raise ValueError("unknown status timezone: %s" % name) from exc


def format_status_timestamp(value, timezone):
    """Convert an RFC 3339 timestamp to the dashboard's display timezone."""
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(timezone).isoformat(sep=" ", timespec="seconds")


def request_profile(path):
    """Return the ThinClient image profile named in a request path."""
    parts = [part for part in path.casefold().split("/") if part]
    try:
        thinclient = parts.index("thinclient")
        profile = parts[thinclient + 1]
    except (ValueError, IndexError):
        return None
    return profile if profile in ("lite", "full") else None


class StatusMonitor:
    """Thread-safe, bounded request history with optional durable state."""

    STATE_VERSION = 1

    def __init__(self, max_clients=256, max_requests=100, state_file=None):
        self.max_clients = max_clients
        self.max_requests = max_requests
        self.started_at = time.time()
        self.history_started_at = self.started_at
        self._started_monotonic = time.monotonic()
        self._lock = threading.Lock()
        self._next_request_id = 1
        self._active = {}
        self._clients = collections.OrderedDict()
        self._requests = collections.deque(maxlen=max_requests)
        self._request_total = 0
        self._successful_total = 0
        self._failed_total = 0
        self._bytes_total = 0
        self._boot_total = 0
        self._server_start_total = 1
        self._recovered_interrupted_total = 0
        self._state_file = os.path.abspath(state_file) if state_file else None
        self._last_saved_at = None
        self._persistence_error = None

        loaded = False
        if self._state_file and os.path.isfile(self._state_file):
            try:
                self._restore_state()
                loaded = True
            except (OSError, TypeError, ValueError, KeyError) as exc:
                self._quarantine_invalid_state(exc)
        if loaded:
            self._server_start_total += 1
        self._recover_interrupted_requests()
        with self._lock:
            self._persist_locked()

    @staticmethod
    def _required_fields(mapping, fields, description):
        if (not isinstance(mapping, dict) or
                not all(field in mapping for field in fields)):
            raise ValueError("invalid %s in status state" % description)

    @staticmethod
    def _nonnegative_int(value, description):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("invalid %s in status state" % description)
        return value

    def _restore_state(self):
        if os.path.getsize(self._state_file) > 8 * 1024 * 1024:
            raise ValueError("status state is unexpectedly large")
        with open(self._state_file, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or data.get("version") != self.STATE_VERSION:
            raise ValueError("unsupported status state version")

        totals = data.get("totals")
        self._required_fields(
            totals,
            ("requests", "successful_requests", "failed_requests", "bytes_sent",
             "boots", "server_starts", "recovered_interrupted_requests"),
            "totals",
        )
        request_total = self._nonnegative_int(totals["requests"], "request total")
        successful_total = self._nonnegative_int(
            totals["successful_requests"], "successful request total"
        )
        failed_total = self._nonnegative_int(
            totals["failed_requests"], "failed request total"
        )
        bytes_total = self._nonnegative_int(totals["bytes_sent"], "byte total")
        boot_total = self._nonnegative_int(totals["boots"], "boot total")
        server_start_total = self._nonnegative_int(
            totals["server_starts"], "server start total"
        )
        recovered_total = self._nonnegative_int(
            totals["recovered_interrupted_requests"], "recovery total"
        )

        history_started_at = data.get("history_started_epoch")
        if not isinstance(history_started_at, (int, float)):
            raise ValueError("invalid history start in status state")

        client_fields = (
            "ip", "mac", "first_seen_epoch", "last_seen_epoch", "last_path",
            "last_status", "profile", "requests", "boots", "bytes_sent",
        )
        clients = collections.OrderedDict()
        stored_clients = data.get("clients", [])
        if not isinstance(stored_clients, list):
            raise ValueError("invalid clients in status state")
        for stored in stored_clients[-self.max_clients:]:
            if not isinstance(stored, list) or len(stored) != 2:
                raise ValueError("invalid client entry in status state")
            key, client = stored
            self._required_fields(client, client_fields, "client entry")
            if not isinstance(key, str) or not isinstance(client["ip"], str):
                raise ValueError("invalid client identity in status state")
            clients[key] = dict(client)

        request_fields = (
            "method", "path", "ip", "mac", "status", "finished_epoch",
            "duration_seconds", "bytes_sent", "interrupted",
            "recovered_after_restart",
        )
        stored_requests = data.get("recent_requests", [])
        if not isinstance(stored_requests, list):
            raise ValueError("invalid requests in status state")
        requests = collections.deque(maxlen=self.max_requests)
        for request in stored_requests[:self.max_requests]:
            self._required_fields(request, request_fields, "request entry")
            requests.append(dict(request))

        active_fields = (
            "id", "method", "path", "ip", "mac", "client_key",
            "started_epoch", "bytes_sent", "content_length",
        )
        stored_active = data.get("active_requests", [])
        if not isinstance(stored_active, list) or len(stored_active) > 2048:
            raise ValueError("invalid active requests in status state")
        active = {}
        for request in stored_active:
            self._required_fields(request, active_fields, "active request")
            request_id = self._nonnegative_int(request["id"], "request id")
            active[request_id] = dict(request)

        next_request_id = self._nonnegative_int(
            data.get("next_request_id"), "next request id"
        )
        self.history_started_at = float(history_started_at)
        self._request_total = request_total
        self._successful_total = successful_total
        self._failed_total = failed_total
        self._bytes_total = bytes_total
        self._boot_total = boot_total
        self._server_start_total = server_start_total
        self._recovered_interrupted_total = recovered_total
        self._clients = clients
        self._requests = requests
        self._active = active
        self._next_request_id = max(next_request_id, max(active, default=0) + 1)
        saved_at = data.get("saved_at_epoch")
        self._last_saved_at = (
            float(saved_at) if isinstance(saved_at, (int, float)) else None
        )

    def _quarantine_invalid_state(self, error):
        stamp = "%s-%s" % (
            datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            time.time_ns(),
        )
        backup = self._state_file + ".corrupt-" + stamp
        try:
            os.replace(self._state_file, backup)
        except OSError as backup_error:
            backup = "could not preserve it (%s)" % backup_error
        sys.stderr.write(
            "thinclient-pxe: WARNING: ignoring invalid HTTP status state (%s); %s\n"
            % (error, backup)
        )
        sys.stderr.flush()

    def _recover_interrupted_requests(self):
        if not self._active:
            return
        recovered_at = time.time()
        for request in list(self._active.values()):
            self._failed_total += 1
            self._recovered_interrupted_total += 1
            self._bytes_total += request["bytes_sent"]
            client = self._clients.get(request["client_key"])
            if client is not None:
                client["last_seen_epoch"] = recovered_at
                client["last_path"] = request["path"]
                client["last_status"] = 0
                client["bytes_sent"] += request["bytes_sent"]
            self._requests.appendleft({
                "method": request["method"],
                "path": request["path"],
                "ip": request["ip"],
                "mac": request["mac"],
                "status": 0,
                "finished_epoch": recovered_at,
                "duration_seconds": max(
                    0.0, recovered_at - request["started_epoch"]
                ),
                "bytes_sent": request["bytes_sent"],
                "interrupted": True,
                "recovered_after_restart": True,
            })
        self._active.clear()

    def _state_document_locked(self, saved_at):
        active = []
        for request in self._active.values():
            item = dict(request)
            item.pop("started_monotonic", None)
            active.append(item)
        return {
            "version": self.STATE_VERSION,
            "saved_at_epoch": saved_at,
            "history_started_epoch": self.history_started_at,
            "next_request_id": self._next_request_id,
            "totals": {
                "requests": self._request_total,
                "successful_requests": self._successful_total,
                "failed_requests": self._failed_total,
                "bytes_sent": self._bytes_total,
                "boots": self._boot_total,
                "server_starts": self._server_start_total,
                "recovered_interrupted_requests": self._recovered_interrupted_total,
            },
            "clients": [[key, dict(client)] for key, client in self._clients.items()],
            "recent_requests": [dict(request) for request in self._requests],
            "active_requests": active,
        }

    def _persist_locked(self):
        if not self._state_file:
            return
        saved_at = time.time()
        directory = os.path.dirname(self._state_file)
        temporary = self._state_file + ".tmp"
        try:
            os.makedirs(directory, mode=0o750, exist_ok=True)
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    self._state_document_locked(saved_at), handle,
                    separators=(",", ":"), sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._state_file)
            if os.name == "posix":
                directory_descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
            self._last_saved_at = saved_at
            self._persistence_error = None
        except (OSError, TypeError, ValueError) as exc:
            self._persistence_error = "%s: %s" % (type(exc).__name__, exc)
            try:
                os.unlink(temporary)
            except OSError:
                pass

    def _persistence_report_locked(self):
        if not self._state_file:
            return {"enabled": False, "status": "disabled", "last_saved_at": None}
        report = {
            "enabled": True,
            "status": "error" if self._persistence_error else "ok",
            "last_saved_at": (
                utc_timestamp(self._last_saved_at)
                if self._last_saved_at is not None else None
            ),
        }
        if self._persistence_error:
            report["error"] = self._persistence_error
        return report

    def persistence_report(self):
        with self._lock:
            return self._persistence_report_locked()

    @staticmethod
    def _client_key(ip, mac):
        return "mac:" + mac if mac else "ip:" + ip

    @staticmethod
    def _new_client(ip, mac, now):
        return {
            "ip": ip,
            "mac": mac,
            "first_seen_epoch": now,
            "last_seen_epoch": now,
            "last_path": None,
            "last_status": None,
            "profile": None,
            "requests": 0,
            "boots": 0,
            "bytes_sent": 0,
        }

    @staticmethod
    def _merge_clients(destination, source):
        destination["first_seen_epoch"] = min(
            destination["first_seen_epoch"], source["first_seen_epoch"]
        )
        if source["last_seen_epoch"] > destination["last_seen_epoch"]:
            for field in ("last_seen_epoch", "last_path", "last_status", "profile"):
                destination[field] = source[field]
        elif destination["profile"] is None and source["profile"] is not None:
            destination["profile"] = source["profile"]
        destination["requests"] += source["requests"]
        destination["boots"] += source["boots"]
        destination["bytes_sent"] += source["bytes_sent"]

    def _touch_client(self, ip, mac, path, now):
        key = self._client_key(ip, mac)
        if mac:
            # Kernel/initrd/root-image requests do not carry a MAC header. When
            # config.json follows from the same address, fold that anonymous
            # boot activity into the now-identified device.
            anonymous_key = self._client_key(ip, None)
            anonymous = self._clients.pop(anonymous_key, None)
            client = self._clients.get(key)
            if client is None:
                client = self._new_client(ip, mac, now)
                self._clients[key] = client
            if anonymous is not None:
                self._merge_clients(client, anonymous)
        else:
            client = self._clients.get(key)
            if client is None:
                client = self._new_client(ip, None, now)
                self._clients[key] = client

        client["ip"] = ip
        client["mac"] = mac or client["mac"]
        client["last_seen_epoch"] = now
        client["last_path"] = path
        profile = request_profile(path)
        if profile:
            client["profile"] = profile
        client["requests"] += 1
        self._clients.move_to_end(key)
        while len(self._clients) > self.max_clients:
            self._clients.popitem(last=False)
        return key

    def begin(self, method, path, ip, mac=None):
        """Register a request and return its opaque request identifier."""
        now = time.time()
        monotonic_now = time.monotonic()
        with self._lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            client_key = self._touch_client(ip, mac, path, now)
            self._active[request_id] = {
                "id": request_id,
                "method": method,
                "path": path,
                "ip": ip,
                "mac": mac,
                "client_key": client_key,
                "started_epoch": now,
                "started_monotonic": monotonic_now,
                "bytes_sent": 0,
                "content_length": None,
            }
            self._request_total += 1
            self._persist_locked()
            return request_id

    def set_content_length(self, request_id, length):
        with self._lock:
            request = self._active.get(request_id)
            if request is not None:
                request["content_length"] = max(0, length)

    def add_bytes(self, request_id, count):
        with self._lock:
            request = self._active.get(request_id)
            if request is not None:
                request["bytes_sent"] += max(0, count)

    def finish(self, request_id, status, interrupted=False):
        """Complete a request and retain its bounded history record."""
        finished_epoch = time.time()
        finished_monotonic = time.monotonic()
        with self._lock:
            request = self._active.pop(request_id, None)
            if request is None:
                return

            status = int(status or 0)
            successful = 200 <= status < 400 and not interrupted
            if successful:
                self._successful_total += 1
            else:
                self._failed_total += 1
            self._bytes_total += request["bytes_sent"]

            client_key = self._client_key(request["ip"], request["mac"])
            client = self._clients.get(client_key)
            if client is not None:
                client["last_seen_epoch"] = finished_epoch
                client["last_path"] = request["path"]
                client["last_status"] = status
                client["bytes_sent"] += request["bytes_sent"]

            is_boot = (
                successful and request["method"] == "GET" and
                os.path.basename(request["path"]).casefold() ==
                "filesystem.squashfs"
            )
            if is_boot:
                self._boot_total += 1
                if client is not None:
                    client["boots"] += 1

            self._requests.appendleft({
                "method": request["method"],
                "path": request["path"],
                "ip": request["ip"],
                "mac": request["mac"],
                "status": status,
                "finished_epoch": finished_epoch,
                "duration_seconds": max(
                    0.0, finished_monotonic - request["started_monotonic"]
                ),
                "bytes_sent": request["bytes_sent"],
                "interrupted": bool(interrupted),
                "recovered_after_restart": False,
            })
            self._persist_locked()

    def snapshot(self):
        """Return a JSON-serialisable point-in-time status snapshot."""
        now = time.time()
        monotonic_now = time.monotonic()
        with self._lock:
            active = []
            for request in self._active.values():
                elapsed = max(0.0, monotonic_now - request["started_monotonic"])
                length = request["content_length"]
                progress = None
                if length:
                    progress = min(100.0, request["bytes_sent"] * 100.0 / length)
                active.append({
                    "id": request["id"],
                    "method": request["method"],
                    "path": request["path"],
                    "ip": request["ip"],
                    "mac": request["mac"],
                    "started_at": utc_timestamp(request["started_epoch"]),
                    "elapsed_seconds": round(elapsed, 3),
                    "bytes_sent": request["bytes_sent"],
                    "content_length": length,
                    "progress_percent": (
                        round(progress, 1) if progress is not None else None
                    ),
                    "bytes_per_second": (
                        round(request["bytes_sent"] / elapsed)
                        if elapsed > 0 else 0
                    ),
                })

            clients = []
            for client in sorted(
                    self._clients.values(),
                    key=lambda item: item["last_seen_epoch"], reverse=True):
                clients.append({
                    "ip": client["ip"],
                    "mac": client["mac"],
                    "first_seen": utc_timestamp(client["first_seen_epoch"]),
                    "last_seen": utc_timestamp(client["last_seen_epoch"]),
                    "last_seen_seconds_ago": round(
                        max(0.0, now - client["last_seen_epoch"]), 1
                    ),
                    "last_path": client["last_path"],
                    "last_status": client["last_status"],
                    "profile": client["profile"],
                    "requests": client["requests"],
                    "boots": client["boots"],
                    "bytes_sent": client["bytes_sent"],
                })

            requests = []
            for request in self._requests:
                item = dict(request)
                item["finished_at"] = utc_timestamp(item.pop("finished_epoch"))
                item["duration_seconds"] = round(item["duration_seconds"], 3)
                requests.append(item)

            return {
                "service": "thinclient-pxe-http",
                "generated_at": utc_timestamp(now),
                "started_at": utc_timestamp(self.started_at),
                "history_started_at": utc_timestamp(self.history_started_at),
                "uptime_seconds": round(
                    max(0.0, monotonic_now - self._started_monotonic), 1
                ),
                "totals": {
                    "requests": self._request_total,
                    "successful_requests": self._successful_total,
                    "failed_requests": self._failed_total,
                    "bytes_sent": self._bytes_total,
                    "boots": self._boot_total,
                    "clients": len(clients),
                    "identified_clients": sum(
                        1 for client in clients if client["mac"]
                    ),
                    "active_transfers": len(active),
                    "server_starts": self._server_start_total,
                    "recovered_interrupted_requests": (
                        self._recovered_interrupted_total
                    ),
                },
                "persistence": self._persistence_report_locked(),
                "active_transfers": active,
                "recent_clients": clients,
                "recent_requests": requests,
            }


def configuration_summary(data):
    """Return ``(summary, error)`` for a decoded configuration document."""
    if not isinstance(data, dict):
        return None, "the top-level JSON value must be an object"

    connections = data.get("connections", [])
    if not isinstance(connections, list):
        return None, "'connections' must be an array"

    hosts = []
    for index, connection in enumerate(connections, 1):
        if not isinstance(connection, dict):
            return None, "connection %d must be an object" % index
        host = connection.get("host", "")
        if not isinstance(host, str):
            return None, "connection %d host must be a string" % index
        hosts.append(host or "?")
    return ", ".join(hosts) or "no connections", None


def health_report(root, persistence=None):
    """Return HTTP service health without reading large PXE artifacts."""
    config = os.path.join(os.path.realpath(root), CONFIG_NAME)
    missing = []
    if not os.path.isdir(root):
        missing.append("PXE root")
    elif not os.path.isfile(config) or not os.access(config, os.R_OK):
        missing.append(CONFIG_NAME)
    problems = []
    if (persistence and persistence.get("enabled") and
            persistence.get("status") != "ok"):
        problems.append("persistent status state is not writable")
    return {
        "status": "ok" if not missing and not problems else "degraded",
        "missing": missing,
        "problems": problems,
    }


def format_bytes(value):
    """Format a byte count compactly for the human status page."""
    amount = float(value or 0)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if abs(amount) < 1024 or unit == units[-1]:
            return ("%.1f %s" if unit != "B" else "%.0f %s") % (amount, unit)
        amount /= 1024


STATUS_SORT_FIELDS = {
    "active.ip": ("active", "ip", "asc"),
    "active.mac": ("active", "mac", "asc"),
    "active.path": ("active", "path", "asc"),
    "active.method": ("active", "method", "asc"),
    "active.progress": ("active", "progress_percent", "desc"),
    "active.speed": ("active", "bytes_per_second", "desc"),
    "active.elapsed": ("active", "elapsed_seconds", "desc"),
    "clients.mac": ("clients", "mac", "asc"),
    "clients.ip": ("clients", "ip", "asc"),
    "clients.profile": ("clients", "profile", "asc"),
    "clients.last_seen": ("clients", "last_seen", "desc"),
    "clients.requests": ("clients", "requests", "desc"),
    "clients.boots": ("clients", "boots", "desc"),
    "clients.bytes": ("clients", "bytes_sent", "desc"),
    "clients.status": ("clients", "last_status", "desc"),
    "clients.path": ("clients", "last_path", "asc"),
    "requests.finished": ("requests", "finished_at", "desc"),
    "requests.ip": ("requests", "ip", "asc"),
    "requests.method": ("requests", "method", "asc"),
    "requests.path": ("requests", "path", "asc"),
    "requests.status": ("requests", "status", "desc"),
    "requests.bytes": ("requests", "bytes_sent", "desc"),
    "requests.duration": ("requests", "duration_seconds", "desc"),
}


def normalize_status_sort(sort_key=None, direction=None):
    """Return a supported dashboard sort and direction, or no sort."""
    if not isinstance(sort_key, str) or sort_key not in STATUS_SORT_FIELDS:
        return None, None
    default_direction = STATUS_SORT_FIELDS[sort_key][2]
    if direction not in ("asc", "desc"):
        direction = default_direction
    return sort_key, direction


def status_sort_from_url(url):
    """Read bounded, allowlisted status sorting options from a request URL."""
    try:
        query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(url).query,
            keep_blank_values=False,
            max_num_fields=8,
        )
    except ValueError:
        return None, None
    sort_key = (query.get("sort") or [None])[0]
    direction = (query.get("dir") or [None])[0]
    if isinstance(sort_key, str):
        sort_key = sort_key[:64]
    if isinstance(direction, str):
        direction = direction[:8]
    return normalize_status_sort(sort_key, direction)


def _status_sort_value(row, field):
    value = row.get(field)
    if field == "ip":
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return (99, str(value).casefold())
        return (address.version, int(address))
    if isinstance(value, str):
        return value.casefold()
    return value


def sort_status_rows(rows, section, sort_key=None, direction=None):
    """Sort one dashboard table without mutating the monitor snapshot."""
    sort_key, direction = normalize_status_sort(sort_key, direction)
    if sort_key is None:
        return list(rows)
    configured_section, field, _default_direction = STATUS_SORT_FIELDS[sort_key]
    if configured_section != section:
        return list(rows)
    present = []
    missing = []
    for row in rows:
        value = row.get(field)
        invalid_ip = False
        if field == "ip" and value not in (None, ""):
            try:
                ipaddress.ip_address(value)
            except ValueError:
                invalid_ip = True
        (missing if value in (None, "") or invalid_ip else present).append(row)
    present.sort(
        key=lambda row: _status_sort_value(row, field),
        reverse=direction == "desc",
    )
    return present + missing


def status_sort_header(label, sort_key, current_sort=None,
                       current_direction=None):
    """Render an accessible table heading that preserves sort on refresh."""
    current_sort, current_direction = normalize_status_sort(
        current_sort, current_direction
    )
    selected = current_sort == sort_key
    default_direction = STATUS_SORT_FIELDS[sort_key][2]
    if selected:
        next_direction = "desc" if current_direction == "asc" else "asc"
        indicator = "▲" if current_direction == "asc" else "▼"
        aria_sort = ' aria-sort="%s"' % (
            "ascending" if current_direction == "asc" else "descending"
        )
        active_class = " active"
    else:
        next_direction = default_direction
        indicator = "↕"
        aria_sort = ' aria-sort="none"'
        active_class = ""
    href = "/status?" + urllib.parse.urlencode({
        "sort": sort_key,
        "dir": next_direction,
    })
    action = "Sort %s %s" % (label, next_direction)
    return (
        '<th scope="col"%s><a class="sort-link%s" href="%s" '
        'aria-label="%s">%s <span aria-hidden="true">%s</span></a></th>'
        % (
            aria_sort, active_class, html.escape(href, quote=True),
            html.escape(action, quote=True), html.escape(label), indicator,
        )
    )


def format_age(seconds):
    """Format a recent age compactly for at-a-glance dashboard reading."""
    try:
        seconds = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        return "unknown"
    if seconds < 5:
        return "just now"
    if seconds < 60:
        return "%ds ago" % seconds
    minutes = seconds // 60
    if minutes < 60:
        return "%dm ago" % minutes
    hours = minutes // 60
    if hours < 48:
        return "%dh ago" % hours
    return "%dd ago" % (hours // 24)


def status_html(snapshot, display_timezone=datetime.timezone.utc,
                display_timezone_name="UTC", sort_key=None,
                sort_direction=None):
    """Render the dependency-free, sortable, auto-refreshing dashboard."""
    escape = lambda value: html.escape(str(value if value is not None else "-"))
    display_time = lambda value: format_status_timestamp(value, display_timezone)
    sort_key, sort_direction = normalize_status_sort(sort_key, sort_direction)
    totals = snapshot["totals"]
    health = snapshot["health"]
    health_class = "ok" if health["status"] == "ok" else "degraded"

    try:
        generated = datetime.datetime.fromisoformat(
            snapshot["generated_at"].replace("Z", "+00:00")
        )
    except (AttributeError, ValueError):
        generated = None

    def seconds_ago(value):
        if generated is None:
            return None
        try:
            moment = datetime.datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except (AttributeError, ValueError):
            return None
        return max(0.0, (generated - moment).total_seconds())

    def time_cell(value, age=None):
        exact = display_time(value)
        if age is None:
            primary, secondary = exact, ""
        else:
            primary = format_age(age)
            secondary = '<span class="cell-note">%s</span>' % escape(exact)
        return (
            '<time datetime="%s" title="%s"><span class="time-main">%s</span>'
            "%s</time>" % (
                escape(value), escape(exact), escape(primary), secondary,
            )
        )

    def status_pill(status, interrupted=False):
        if interrupted and status == 0:
            label = "interrupted"
        else:
            label = str(status if status is not None else "-")
            if interrupted:
                label += " interrupted"
        good = isinstance(status, int) and 200 <= status < 400 and not interrupted
        css_class = "good" if good else ("bad" if status is not None else "neutral")
        return '<span class="status-code %s">%s</span>' % (
            css_class, escape(label)
        )

    active_rows = []
    active_transfers = sort_status_rows(
        snapshot["active_transfers"], "active", sort_key, sort_direction
    )
    for transfer in active_transfers:
        percent = transfer["progress_percent"]
        if percent is None:
            progress = '<span class="time-main">%s sent</span>' % escape(
                format_bytes(transfer["bytes_sent"])
            )
        else:
            percent = min(100.0, max(0.0, float(percent)))
            progress_label = "%s / %s (%.1f%%)" % (
                format_bytes(transfer["bytes_sent"]),
                format_bytes(transfer["content_length"]), percent,
            )
            progress = (
                '<div class="progress" role="progressbar" aria-label="%s" '
                'aria-valuemin="0" aria-valuemax="100" aria-valuenow="%.1f">'
                '<span style="width:%.1f%%"></span></div>'
                '<span class="cell-note">%s</span>' % (
                    escape(progress_label), percent, percent,
                    escape(progress_label),
                )
            )
        active_rows.append(
            "<tr><td><code>%s</code></td><td><code>%s</code></td>"
            '<td><code class="path" title="%s">%s</code></td>'
            '<td><span class="method">%s</span></td><td>%s</td>'
            '<td><span class="time-main">%s/s</span></td><td>%.1fs</td></tr>' % (
                escape(transfer["ip"]), escape(transfer["mac"]),
                escape(transfer["path"]), escape(transfer["path"]),
                escape(transfer["method"]), progress,
                escape(format_bytes(transfer["bytes_per_second"])),
                transfer["elapsed_seconds"],
            )
        )
    if not active_rows:
        active_rows.append(
            '<tr><td colspan="7" class="empty"><strong>No active transfers</strong>'
            '<span>Waiting for the next PXE client download.</span></td></tr>'
        )

    client_rows = []
    clients = sort_status_rows(
        snapshot["recent_clients"][:100], "clients", sort_key, sort_direction
    )
    for client in clients:
        last_status = client["last_status"]
        row_class = ' class="row-error"' if (
            isinstance(last_status, int) and not 200 <= last_status < 400
        ) else ""
        client_rows.append(
            '<tr%s><td><code>%s</code></td><td><code>%s</code></td>'
            '<td><span class="profile">%s</span></td><td>%s</td>'
            '<td>%s</td><td>%s</td><td>%s</td><td>%s</td>'
            '<td><code class="path" title="%s">%s</code></td></tr>' % (
                row_class, escape(client["mac"]), escape(client["ip"]),
                escape(client["profile"] or "-"),
                time_cell(client["last_seen"], client["last_seen_seconds_ago"]),
                escape(client["requests"]), escape(client["boots"]),
                escape(format_bytes(client["bytes_sent"])),
                status_pill(last_status), escape(client["last_path"]),
                escape(client["last_path"]),
            )
        )
    if not client_rows:
        client_rows.append(
            '<tr><td colspan="9" class="empty"><strong>No clients seen</strong>'
            '<span>Client inventory appears after the first HTTP request.</span></td></tr>'
        )

    request_rows = []
    requests = sort_status_rows(
        snapshot["recent_requests"][:50], "requests", sort_key, sort_direction
    )
    for request in requests:
        request_ok = 200 <= request["status"] < 400 and not request["interrupted"]
        row_class = "" if request_ok else ' class="row-error"'
        request_rows.append(
            '<tr%s><td>%s</td><td><code>%s</code></td>'
            '<td><span class="method">%s</span></td>'
            '<td><code class="path" title="%s">%s</code></td>'
            '<td>%s</td><td>%s</td><td>%.2fs</td></tr>' % (
                row_class,
                time_cell(request["finished_at"], seconds_ago(request["finished_at"])),
                escape(request["ip"]), escape(request["method"]),
                escape(request["path"]), escape(request["path"]),
                status_pill(request["status"], request["interrupted"]),
                escape(format_bytes(request["bytes_sent"])),
                request["duration_seconds"],
            )
        )
    if not request_rows:
        request_rows.append(
            '<tr><td colspan="7" class="empty"><strong>No requests recorded</strong>'
            '<span>Health and status probes are intentionally excluded.</span></td></tr>'
        )

    active_headers = "".join((
        status_sort_header("IP address", "active.ip", sort_key, sort_direction),
        status_sort_header("MAC", "active.mac", sort_key, sort_direction),
        status_sort_header("Path", "active.path", sort_key, sort_direction),
        status_sort_header("Method", "active.method", sort_key, sort_direction),
        status_sort_header("Progress", "active.progress", sort_key, sort_direction),
        status_sort_header("Speed", "active.speed", sort_key, sort_direction),
        status_sort_header("Elapsed", "active.elapsed", sort_key, sort_direction),
    ))
    client_headers = "".join((
        status_sort_header("MAC", "clients.mac", sort_key, sort_direction),
        status_sort_header("IP address", "clients.ip", sort_key, sort_direction),
        status_sort_header("Profile", "clients.profile", sort_key, sort_direction),
        status_sort_header(
            "Last seen (%s)" % display_timezone_name,
            "clients.last_seen", sort_key, sort_direction,
        ),
        status_sort_header("Requests", "clients.requests", sort_key, sort_direction),
        status_sort_header("Downloads", "clients.boots", sort_key, sort_direction),
        status_sort_header("Data", "clients.bytes", sort_key, sort_direction),
        status_sort_header("Last status", "clients.status", sort_key, sort_direction),
        status_sort_header("Last path", "clients.path", sort_key, sort_direction),
    ))
    request_headers = "".join((
        status_sort_header(
            "Finished (%s)" % display_timezone_name,
            "requests.finished", sort_key, sort_direction,
        ),
        status_sort_header("IP address", "requests.ip", sort_key, sort_direction),
        status_sort_header("Method", "requests.method", sort_key, sort_direction),
        status_sort_header("Path", "requests.path", sort_key, sort_direction),
        status_sort_header("Status", "requests.status", sort_key, sort_direction),
        status_sort_header("Sent", "requests.bytes", sort_key, sort_direction),
        status_sort_header("Duration", "requests.duration", sort_key, sort_direction),
    ))

    alerts = []
    if health["missing"]:
        alerts.append("Missing or unreadable: %s" % ", ".join(health["missing"]))
    alerts.extend(health["problems"])
    alerts_html = "" if not alerts else (
        '<div class="alerts" role="alert">%s</div>' % "".join(
            '<p>%s</p>' % escape(message) for message in alerts
        )
    )

    def summary_card(label, value, note, css_class=""):
        return (
            '<article class="card %s"><span>%s</span><strong>%s</strong>'
            '<small>%s</small></article>' % (
                css_class, escape(label), escape(value), escape(note),
            )
        )

    failure_class = "danger" if totals["failed_requests"] else "healthy"
    cards = "".join((
        summary_card("Active transfers", totals["active_transfers"],
                     "downloading now", "live" if totals["active_transfers"] else ""),
        summary_card("Clients seen", totals["clients"],
                     "%s identified by MAC" % totals["identified_clients"]),
        summary_card("Root downloads", totals["boots"], "completed successfully"),
        summary_card("Requests", totals["requests"],
                     "%s successful" % totals["successful_requests"]),
        summary_card("Failures", totals["failed_requests"],
                     "none recorded" if not totals["failed_requests"] else "review rows below",
                     failure_class),
        summary_card("Data served", format_bytes(totals["bytes_sent"]),
                     "HTTP payload total"),
    ))

    if sort_key:
        refresh_url = "/status?" + urllib.parse.urlencode({
            "sort": sort_key, "dir": sort_direction,
        })
    else:
        refresh_url = "/status"

    return ("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta http-equiv="refresh" content="10">
<title>ThinClient PXE status</title>
<style>
:root { color-scheme:dark; --bg:#07101d; --panel:#101b2d; --panel-2:#142238;
  --line:#273b58; --line-soft:#1d2d45; --text:#ecf3fc; --muted:#9fb0c7;
  --green:#45d692; --amber:#ffc15c; --red:#ff7180; --blue:#70b2ff; }
* { box-sizing:border-box; } body { margin:0; min-height:100vh; color:var(--text);
  background:radial-gradient(circle at 15%% -10%%,#16355d 0,transparent 34rem),var(--bg);
  font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif; }
main { width:min(1560px,96vw); margin:0 auto; padding:30px 0 48px; }
header { display:flex; align-items:flex-start; justify-content:space-between; gap:24px;
  padding:4px 2px 18px; } h1 { margin:2px 0 6px; font-size:clamp(24px,3vw,34px);
  letter-spacing:-.025em; } h2 { margin:0; font-size:18px; } p { margin:0; color:var(--muted); }
.eyebrow { color:var(--blue); font-size:11px; font-weight:800; letter-spacing:.13em;
  text-transform:uppercase; }.header-actions { display:flex; align-items:center; gap:9px;
  flex-wrap:wrap; justify-content:flex-end; }.button { padding:8px 11px; color:var(--text);
  text-decoration:none; border:1px solid var(--line); border-radius:9px; background:#11223a; }
.button:hover { border-color:var(--blue); }.badge { display:inline-flex; align-items:center;
  gap:8px; padding:8px 12px; border:1px solid var(--line); border-radius:999px;
  text-transform:uppercase; font-size:12px; font-weight:800; letter-spacing:.06em; }
.badge::before { content:""; width:9px; height:9px; border-radius:50%%; background:currentColor;
  box-shadow:0 0 0 4px #ffffff0d; }.badge.ok { color:var(--green); }
.badge.degraded { color:var(--amber); }.alerts { margin:0 0 14px; padding:12px 14px;
  border:1px solid #8b682e; border-radius:10px; background:#3b2d173d; }
.alerts p { color:var(--amber); }.cards { display:grid;
  grid-template-columns:repeat(auto-fit,minmax(155px,1fr)); gap:11px; margin:0 0 15px; }
.card,.panel { background:linear-gradient(180deg,var(--panel-2),var(--panel));
  border:1px solid var(--line); border-radius:13px; box-shadow:0 12px 32px #0003; }
.card { min-height:112px; padding:16px; border-top:3px solid var(--line); }
.card.live { border-top-color:var(--blue); }.card.healthy { border-top-color:var(--green); }
.card.danger { border-top-color:var(--red); }.card>span { display:block; color:var(--muted);
  font-size:12px; font-weight:700; }.card strong { display:block; margin:5px 0 2px;
  font-size:25px; font-variant-numeric:tabular-nums; }.card small { color:var(--muted); }
.panel { margin-top:13px; overflow:hidden; }.panel-title { display:flex; align-items:center;
  justify-content:space-between; gap:16px; padding:16px 17px 12px; }.panel-title p {
  margin-top:3px; font-size:12px; }.count { min-width:32px; padding:4px 9px;
  border:1px solid var(--line); border-radius:999px; color:var(--muted); text-align:center;
  font-variant-numeric:tabular-nums; }.table-wrap { overflow:auto; border-top:1px solid var(--line-soft); }
table { width:100%%; border-collapse:separate; border-spacing:0; white-space:nowrap; }
th,td { padding:10px 11px; text-align:left; border-bottom:1px solid var(--line-soft);
  font-variant-numeric:tabular-nums; } th { position:sticky; top:0; z-index:1;
  background:#0e192a; color:var(--muted); font-size:11px; text-transform:uppercase;
  letter-spacing:.055em; } tbody tr:nth-child(even) { background:#ffffff05; }
tbody tr:hover { background:#70b2ff0d; } tbody tr:last-child td { border-bottom:0; }
.row-error { background:#ff71800a !important; }.sort-link { display:flex; align-items:center;
  justify-content:space-between; gap:8px; min-height:24px; color:inherit; text-decoration:none; }
.sort-link:hover,.sort-link.active { color:var(--blue); }.sort-link span { font-size:10px; }
code { color:#cce1ff; font:12px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace; }
.path { display:block; max-width:430px; overflow:hidden; text-overflow:ellipsis; }
.method,.profile { display:inline-block; padding:2px 7px; border:1px solid var(--line);
  border-radius:6px; color:#d6e6fa; background:#0a1524; font-size:11px; font-weight:700; }
.status-code { display:inline-block; min-width:42px; padding:3px 7px; border-radius:999px;
  text-align:center; font-size:11px; font-weight:800; }.status-code.good { color:var(--green);
  background:#45d69216; border:1px solid #45d69242; }.status-code.bad { color:var(--red);
  background:#ff718016; border:1px solid #ff718042; }.status-code.neutral { color:var(--muted);
  border:1px solid var(--line); }.time-main { display:block; color:var(--text); }
.cell-note { display:block; margin-top:2px; color:var(--muted); font-size:11px; }
.progress { width:170px; height:7px; overflow:hidden; border-radius:999px;
  background:#07101d; border:1px solid var(--line-soft); }.progress span { display:block;
  height:100%%; border-radius:inherit; background:linear-gradient(90deg,var(--blue),var(--green)); }
.empty { padding:28px !important; color:var(--muted); text-align:center; }
.empty strong,.empty span { display:block; }.empty strong { color:var(--text); margin-bottom:3px; }
footer { display:flex; flex-wrap:wrap; gap:6px 12px; color:var(--muted); margin-top:16px;
  padding:0 3px; font-size:12px; } footer a { color:var(--blue); }
.sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px;
  overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
a:focus-visible { outline:2px solid var(--blue); outline-offset:2px; }
@media(max-width:760px) { main { width:min(100%% - 20px,1560px); padding-top:18px; }
  header { flex-direction:column; }.header-actions { justify-content:flex-start; }
  .cards { grid-template-columns:repeat(2,minmax(0,1fr)); }.card { min-height:102px; }
  .panel-title { align-items:flex-start; }.path { max-width:280px; } }
@media(max-width:430px) { .cards { grid-template-columns:1fr; }.card { min-height:auto; }
  .header-actions { width:100%%; }.button { flex:1; text-align:center; } }
</style>
</head>
<body><main>
<header><div><span class="eyebrow">Live PXE fleet monitor</span><h1>ThinClient PXE HTTP</h1>
<p>Service up since %s · retained history since %s · timezone %s</p></div>
<div class="header-actions"><a class="button" href="%s">Refresh now</a>
<a class="button" href="/status.json">JSON</a><div class="badge %s" aria-live="polite">%s</div></div></header>
%s
<section class="cards" aria-label="Service summary">%s</section>
<section class="panel"><div class="panel-title"><div><h2>Active transfers</h2>
<p>Live progress and throughput. Select a heading to sort.</p></div><span class="count">%s</span></div>
<div class="table-wrap"><table><caption class="sr-only">Active HTTP transfers</caption>
<thead><tr>%s</tr></thead><tbody>%s</tbody></table></div></section>
<section class="panel"><div class="panel-title"><div><h2>Recently seen clients</h2>
<p>Most recent 100 clients. Sorting survives automatic refresh.</p></div><span class="count">%s</span></div>
<div class="table-wrap"><table><caption class="sr-only">Recently seen clients</caption>
<thead><tr>%s</tr></thead><tbody>%s</tbody></table></div></section>
<section class="panel"><div class="panel-title"><div><h2>Recent requests</h2>
<p>Latest 50 PXE HTTP requests; failures are highlighted.</p></div><span class="count">%s</span></div>
<div class="table-wrap"><table><caption class="sr-only">Recent HTTP requests</caption>
<thead><tr>%s</tr></thead><tbody>%s</tbody></table></div></section>
<footer><span>Updated %s (%s)</span><span>Refreshes every 10 seconds</span>
<span>Persistent state: %s</span><span>Service starts: %s</span></footer>
</main></body></html>
""") % (
        escape(display_time(snapshot["started_at"])),
        escape(display_time(snapshot["history_started_at"])),
        escape(display_timezone_name), escape(refresh_url), health_class,
        escape(health["status"]), alerts_html, cards,
        len(active_transfers), active_headers, "".join(active_rows),
        len(clients), client_headers, "".join(client_rows),
        len(requests), request_headers, "".join(request_rows),
        escape(display_time(snapshot["generated_at"])),
        escape(display_timezone_name), escape(snapshot["persistence"]["status"]),
        escape(totals["server_starts"]),
    )


class Handler(http.server.SimpleHTTPRequestHandler):
    root = "."
    status_monitor = StatusMonitor()
    status_timezone = datetime.timezone.utc
    status_timezone_name = "UTC"

    @staticmethod
    def _decoded_url_path(path):
        """Decode an HTTP path once, rejecting invalid UTF-8 and NUL bytes."""
        try:
            decoded = urllib.parse.unquote(
                urllib.parse.urlsplit(path).path, errors="strict"
            )
        except (UnicodeDecodeError, ValueError):
            return None
        return None if "\0" in decoded else decoded

    def _path_in_root(self, decoded):
        """Map a decoded URL path into the served tree, or return None."""
        root = os.path.realpath(self.root)
        clean = os.path.normpath(decoded).lstrip("/\\")
        full = os.path.realpath(os.path.join(root, clean))
        try:
            if os.path.commonpath([full, root]) != root:
                return None
        except ValueError:              # Different drives on Windows.
            return None
        return full

    def translate_path(self, path):
        # Deliberately not using the base implementation: it resolves against
        # the process working directory, and this server is usually pointed at
        # a tree somewhere else.
        decoded = self._decoded_url_path(path)
        full = self._path_in_root(decoded) if decoded is not None else None
        # send_head rejects invalid paths before this normally matters. Keep a
        # harmless in-root fallback for callers of translate_path itself.
        return full or os.path.join(os.path.realpath(self.root), ".invalid-request")

    def send_head(self):
        decoded = self._decoded_url_path(self.path)
        if decoded is None or self._path_in_root(decoded) is None:
            self.send_error(404, "File not found")
            return None

        name = os.path.basename(decoded)
        if (name.casefold().startswith("config-") and
                not getattr(self, "_serving_selected_config", False)):
            # Overrides are selected only through /config.json plus the MAC
            # header. Do not make them enumerable static files as well.
            self.send_error(404, "File not found")
            return None
        return super().send_head()

    def list_directory(self, path):
        """Do not expose the PXE tree or its per-device configuration names."""
        self.send_error(404, "File not found")
        return None

    def _serve_with_config_selection(self, serve):
        """Apply the same per-device selection to GET and HEAD."""
        original_path = self.path
        previous_selection = getattr(self, "_serving_selected_config", False)
        self._serving_selected_config = False
        try:
            requested = self._decoded_url_path(original_path)
            if requested is not None and os.path.basename(requested) == CONFIG_NAME:
                served = self._config_for_client(requested)
                if served:
                    self.path = "/" + os.path.relpath(
                        served, os.path.realpath(self.root)
                    ).replace(os.sep, "/")
                    self._serving_selected_config = True
            return serve()
        finally:
            self.path = original_path
            self._serving_selected_config = previous_selection

    def _request_path(self):
        decoded = self._decoded_url_path(self.path)
        if decoded is None:
            decoded = urllib.parse.urlsplit(self.path).path
        # Never retain query strings, and bound memory even for hostile paths.
        return decoded[:512]

    def _client_mac(self):
        mac = (self.headers.get("X-ThinClient-MAC") or "").strip().lower()
        return mac if MAC_RE.fullmatch(mac) else None

    def _serve_monitored(self, serve):
        self._monitor_status = None
        self._monitor_request_id = self.status_monitor.begin(
            self.command, self._request_path(), self.client_address[0],
            self._client_mac(),
        )
        interrupted = False
        try:
            return serve()
        except ConnectionError:
            interrupted = True
            # PXE clients can disappear during a transfer because of a reboot
            # or firmware retry. Record the partial transfer without emitting
            # a full socket traceback for this routine network condition.
            return None
        except OSError:
            interrupted = True
            raise
        finally:
            self.status_monitor.finish(
                self._monitor_request_id, self._monitor_status, interrupted
            )
            self._monitor_request_id = None

    def _status_snapshot(self):
        snapshot = self.status_monitor.snapshot()
        snapshot["health"] = health_report(
            self.root, snapshot["persistence"]
        )
        snapshot["status"] = snapshot["health"]["status"]
        return snapshot

    def _send_monitor_response(self, status, content_type, body, head_only):
        body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if content_type.startswith("text/html"):
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; "
                "base-uri 'none'; frame-ancestors 'none'",
            )
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _serve_monitor_endpoint(self, head_only=False):
        path = self._request_path()
        if path not in STATUS_PATHS:
            return False
        if path == "/healthz":
            persistence = self.status_monitor.persistence_report()
            health = health_report(self.root, persistence)
            body = json.dumps({
                "service": "thinclient-pxe-http",
                "status": health["status"],
                "missing": health["missing"],
                "problems": health["problems"],
                "persistence": persistence,
            }, sort_keys=True) + "\n"
            self._send_monitor_response(
                200 if health["status"] == "ok" else 503,
                "application/json; charset=utf-8", body, head_only,
            )
        elif path == "/status.json":
            body = json.dumps(
                self._status_snapshot(), indent=2, sort_keys=True
            ) + "\n"
            self._send_monitor_response(
                200, "application/json; charset=utf-8", body, head_only
            )
        else:
            sort_key, sort_direction = status_sort_from_url(self.path)
            self._send_monitor_response(
                200, "text/html; charset=utf-8",
                status_html(
                    self._status_snapshot(), self.status_timezone,
                    self.status_timezone_name, sort_key, sort_direction,
                ), head_only,
            )
        return True

    def do_GET(self):
        if self._serve_monitor_endpoint():
            return None
        return self._serve_monitored(
            lambda: self._serve_with_config_selection(
                lambda: http.server.SimpleHTTPRequestHandler.do_GET(self)
            )
        )

    def do_HEAD(self):
        if self._serve_monitor_endpoint(head_only=True):
            return None
        return self._serve_monitored(
            lambda: self._serve_with_config_selection(
                lambda: http.server.SimpleHTTPRequestHandler.do_HEAD(self)
            )
        )

    def send_response(self, code, message=None):
        if getattr(self, "_monitor_request_id", None) is not None:
            self._monitor_status = code
        return super().send_response(code, message)

    def send_header(self, keyword, value):
        if (keyword.casefold() == "content-length" and
                getattr(self, "_monitor_request_id", None) is not None):
            try:
                self.status_monitor.set_content_length(
                    self._monitor_request_id, int(value)
                )
            except ValueError:
                pass
        return super().send_header(keyword, value)

    def copyfile(self, source, outputfile):
        request_id = getattr(self, "_monitor_request_id", None)
        if request_id is None:
            return super().copyfile(source, outputfile)
        while True:
            chunk = source.read(256 * 1024)
            if not chunk:
                return None
            outputfile.write(chunk)
            self.status_monitor.add_bytes(request_id, len(chunk))

    def _config_for_client(self, requested):
        """Prefer a per-MAC file; this is selection, not authentication."""
        mac = (self.headers.get("X-ThinClient-MAC") or "").strip().lower()
        if not MAC_RE.fullmatch(mac):
            return None
        requested_path = self._path_in_root(requested)
        if requested_path is None:
            return None
        directory = os.path.dirname(requested_path)
        candidate = os.path.join(directory, "config-%s.json" % mac.replace(":", "-"))
        return candidate if os.path.isfile(candidate) else None

    def log_message(self, fmt, *args):
        if self._request_path() in STATUS_PATHS:
            return
        mac = self.headers.get("X-ThinClient-MAC", "-")
        sys.stdout.write("%s  %-17s %s  %s\n" % (
            time.strftime("%H:%M:%S"), mac, self.address_string(), fmt % args))
        sys.stdout.flush()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    # A classroom/lab can power on dozens of PXE clients together. The stdlib
    # default backlog is only 5, which can reject connections during that boot
    # burst even though each accepted transfer runs in its own worker thread.
    request_queue_size = 128


def local_addresses():
    addresses = []
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("192.0.2.1", 9))
        addresses.append(sock.getsockname()[0])
    except OSError:
        pass
    finally:
        if sock is not None:
            sock.close()
    return addresses


def main():
    parser = argparse.ArgumentParser(description="ThinClient configuration server")
    parser.add_argument("--root", default="out/pxe",
                        help="directory to serve (default: out/pxe)")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument(
        "--state-file",
        help="persist bounded HTTP status history to this crash-safe JSON file",
    )
    parser.add_argument(
        "--status-timezone", default="UTC",
        help="IANA timezone for the HTML status page (default: UTC)",
    )
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        sys.exit("no such directory: %s" % root)
    try:
        status_timezone = resolve_status_timezone(args.status_timezone)
    except ValueError as exc:
        parser.error(str(exc))
    Handler.root = root
    Handler.status_monitor = StatusMonitor(state_file=args.state_file)
    Handler.status_timezone = status_timezone
    Handler.status_timezone_name = args.status_timezone.strip()

    config = os.path.join(root, CONFIG_NAME)
    print("serving %s on port %d" % (root, args.port))
    if args.state_file:
        print("  persistent status -> %s" % os.path.abspath(args.state_file))
    print("  status timezone -> %s" % Handler.status_timezone_name)
    if os.path.isfile(config):
        try:
            with open(config, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            print("  WARNING: %s is not valid JSON (%s)" % (CONFIG_NAME, exc))
        else:
            summary, error = configuration_summary(data)
            if error:
                print("  WARNING: %s is not a valid ThinClient config (%s)" %
                      (CONFIG_NAME, error))
            else:
                print("  %s -> %s" % (CONFIG_NAME, summary))
    else:
        print("  WARNING: %s is not present; clients will keep their built-in config"
              % CONFIG_NAME)

    per_device = [
        filename for filename in os.listdir(root)
        if PER_DEVICE_CONFIG_RE.fullmatch(filename) and
        os.path.isfile(os.path.join(root, filename))
    ]
    if per_device:
        print("  per-device overrides: %s" % ", ".join(sorted(per_device)))

    for address in local_addresses():
        print("\n  DHCP option 224:  http://%s:%d/%s" % (address, args.port, CONFIG_NAME))
        print("  or kernel arg  :  tc.config=http://%s:%d/%s"
              % (address, args.port, CONFIG_NAME))
        print("  status monitor :  http://%s:%d/status" % (address, args.port))
    print("\nCtrl+C to stop\n")

    with Server((args.bind, args.port), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")


if __name__ == "__main__":
    main()
