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

CONFIG_NAME = "config.json"


class Handler(http.server.SimpleHTTPRequestHandler):
    root = "."

    def translate_path(self, path):
        # Deliberately not using the base implementation: it resolves against
        # the process working directory, and this server is usually pointed at
        # a tree somewhere else.
        clean = path.split("?", 1)[0].split("#", 1)[0]
        clean = os.path.normpath(clean).lstrip("/\\")
        full = os.path.join(self.root, clean)
        # Refuse anything that climbs out of the served tree.
        if os.path.commonpath([os.path.abspath(full), os.path.abspath(self.root)]) \
                != os.path.abspath(self.root):
            return os.path.join(self.root, "does-not-exist")
        return full

    def do_GET(self):
        requested = self.path.split("?", 1)[0].lstrip("/")
        if os.path.basename(requested) == CONFIG_NAME:
            served = self._config_for_client(requested)
            if served:
                self.path = "/" + os.path.relpath(served, self.root).replace(os.sep, "/")
        return super().do_GET()

    def _config_for_client(self, requested):
        """Prefer a per-MAC file when the client identified itself."""
        mac = (self.headers.get("X-ThinClient-MAC") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}", mac or ""):
            return None
        directory = os.path.dirname(os.path.join(self.root, requested))
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


def local_addresses():
    addresses = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("192.0.2.1", 9))
        addresses.append(sock.getsockname()[0])
        sock.close()
    except OSError:
        pass
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
            data = json.load(open(config, encoding="utf-8"))
            hosts = ", ".join(c.get("host", "?") for c in data.get("connections", []))
            print("  %s -> %s" % (CONFIG_NAME, hosts or "no connections"))
        except (OSError, ValueError) as exc:
            print("  WARNING: %s is not valid JSON (%s)" % (CONFIG_NAME, exc))
    else:
        print("  WARNING: %s is not present; clients will keep their built-in config"
              % CONFIG_NAME)

    per_device = [f for f in os.listdir(root) if f.startswith("config-")]
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
