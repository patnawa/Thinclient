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
import http.server
import os
import re
import socket
import socketserver
import sys
import time
import urllib.parse

CONFIG_NAME = "config.json"
MAC_RE = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}\Z", re.IGNORECASE)
PER_DEVICE_CONFIG_RE = re.compile(
    r"config-[0-9a-f]{2}(?:-[0-9a-f]{2}){5}\.json\Z"
)


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


class Handler(http.server.SimpleHTTPRequestHandler):
    root = "."

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

    def do_GET(self):
        return self._serve_with_config_selection(super().do_GET)

    def do_HEAD(self):
        return self._serve_with_config_selection(super().do_HEAD)

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
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        sys.exit("no such directory: %s" % root)
    Handler.root = root

    config = os.path.join(root, CONFIG_NAME)
    print("serving %s on port %d" % (root, args.port))
    if os.path.isfile(config):
        try:
            import json
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
    print("\nCtrl+C to stop\n")

    with Server((args.bind, args.port), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")


if __name__ == "__main__":
    main()
