"""Small, bounded network preflight helpers for the GTK support dialog."""

import ipaddress
import json
import socket
import subprocess
import re

import rdpprobe

IP = "/usr/sbin/ip"
GETENT = "/usr/bin/getent"
PING = "/usr/bin/ping"


def _clean_label(value, fallback="Configured server"):
    text = " ".join(str(value or "").split())[:80]
    return text or fallback


def _split_endpoint(value, default_port, allow_embedded_port=False):
    raw = str(value or "").strip()
    if not raw or len(raw) > 253 or any(ord(ch) < 33 for ch in raw):
        raise ValueError("server address is missing or invalid")
    if "://" in raw or any(ch in raw for ch in "/?#@"):
        raise ValueError("enter a hostname or IP address, not a URL")
    port = default_port
    if raw.startswith("["):
        close = raw.find("]")
        if close < 0:
            raise ValueError("invalid bracketed IPv6 address")
        host = raw[1:close]
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            raise ValueError("brackets are only valid around IPv6 addresses") from None
        suffix = raw[close + 1:]
        if suffix:
            if (not allow_embedded_port or not suffix.startswith(":")
                    or not suffix[1:].isdigit()):
                raise ValueError("invalid server port")
            port = int(suffix[1:])
    elif raw.count(":") == 1:
        candidate, suffix = raw.rsplit(":", 1)
        if suffix.isdigit() and allow_embedded_port:
            host, port = candidate, int(suffix)
        elif suffix.isdigit():
            raise ValueError("put the server port in the Port field")
        else:
            host = raw
    else:
        host = raw
    if not (1 <= int(port) <= 65535):
        raise ValueError("server port must be between 1 and 65535")
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        try:
            host = host.rstrip(".").encode("idna").decode("ascii").lower()
        except (UnicodeError, ValueError):
            raise ValueError("invalid server hostname") from None
        labels = host.split(".")
        if (not host or len(host) > 253 or any(
                not label or len(label) > 63 or label[0] == "-" or label[-1] == "-"
                or not all(ch.isalnum() or ch == "-" for ch in label)
                for label in labels)):
            raise ValueError("invalid server hostname")
    return host, int(port)


def normalize_target(connection):
    """Return a validated, non-secret effective endpoint for diagnostics."""
    protocol = str(connection.get("protocol") or "rdp").lower()
    if protocol not in ("rdp", "vnc"):
        raise ValueError("unsupported connection protocol")
    raw_port = connection.get("port")
    if isinstance(raw_port, bool) or raw_port in (None, ""):
        raise ValueError("invalid server port")
    try:
        configured_port = int(raw_port)
    except (TypeError, ValueError):
        raise ValueError("invalid server port") from None
    if protocol == "vnc" and configured_port == 3389:
        configured_port = 5900
    configured_host, configured_port = _split_endpoint(
        connection.get("host"), configured_port, allow_embedded_port=False
    )
    via_gateway = bool(protocol == "rdp" and connection.get("gateway"))
    if via_gateway:
        host, port = _split_endpoint(connection.get("gateway"), 443,
                                     allow_embedded_port=True)
    else:
        host, port = configured_host, configured_port
    return {
        "name": _clean_label(connection.get("name")),
        "protocol": protocol,
        "host": host,
        "port": port,
        "via_gateway": via_gateway,
        "configured_host": configured_host,
        "configured_port": configured_port,
    }


def parse_local_network(address_json, route_json):
    """Return an active interface/address and the normal default gateway."""
    empty = {"interface": "", "address": ""}
    try:
        addresses = json.loads(address_json)
        routes = json.loads(route_json)
    except (TypeError, ValueError):
        return empty, ""
    if not isinstance(addresses, list) or not isinstance(routes, list):
        return empty, ""

    defaults = [route for route in routes
                if isinstance(route, dict)
                and route.get("dst") == "default"
                and isinstance(route.get("dev"), str)]
    default = min(defaults, key=lambda route: route.get("metric", 0)) \
        if defaults else None
    interface = default["dev"] if default else ""
    gateway = (default.get("gateway")
               if default and isinstance(default.get("gateway"), str) else "")
    candidates = [record for record in addresses
                  if isinstance(record, dict)
                  and record.get("ifname") != "lo"
                  and (not interface or record.get("ifname") == interface)]
    # Prefer IPv4 for the compact support report, but do not describe an
    # IPv6-only client as disconnected. Global IPv6 is more useful than a
    # link-local address when both exist.
    for family, global_only in (("inet", False), ("inet6", True),
                                ("inet6", False)):
        for record in candidates:
            for item in record.get("addr_info", []):
                if not isinstance(item, dict) or item.get("family") != family:
                    continue
                if global_only and item.get("scope") not in (None, "global"):
                    continue
                local = item.get("local")
                prefix = item.get("prefixlen")
                if isinstance(local, str) and isinstance(prefix, int):
                    return {"interface": record.get("ifname", interface),
                            "address": "%s/%d" % (local, prefix)}, gateway
    return {"interface": interface, "address": ""}, gateway


def check_local_network(runner=subprocess.run):
    """Read local/default state independently of the selected destination."""
    outputs = []
    for argv in ([IP, "-j", "address", "show"],
                 [IP, "-j", "route", "show", "default"]):
        try:
            result = runner(argv, capture_output=True, text=True, timeout=5,
                            check=False)
            outputs.append(result.stdout if result.returncode == 0 else "")
        except (OSError, subprocess.SubprocessError):
            outputs.append("")
    return parse_local_network(*outputs)


def _detail(error):
    text = str(error).strip().replace("\n", " ")
    return text[:180] or error.__class__.__name__


def check_dns(host, runner=subprocess.run):
    """Resolve a hostname with the system NSS configuration and a hard limit."""
    try:
        ipaddress.ip_address(host)
        return {"ok": True, "detail": "not needed (IP address)",
                "addresses": [str(ipaddress.ip_address(host))]}
    except ValueError:
        pass
    try:
        result = runner(
            [GETENT, "ahosts", "--", host], capture_output=True, text=True, timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            addresses = []
            for line in result.stdout.splitlines():
                fields = line.split()
                if not fields:
                    continue
                try:
                    address = str(ipaddress.ip_address(fields[0]))
                except ValueError:
                    continue
                if address not in addresses:
                    addresses.append(address)
                if len(addresses) == 8:
                    break
            if addresses:
                return {"ok": True, "detail": ", ".join(addresses),
                        "addresses": addresses}
        return {"ok": False, "detail": (result.stderr or "name not found").strip()[:180],
                "addresses": []}
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "lookup timed out", "addresses": []}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "detail": _detail(exc), "addresses": []}


def check_ping(gateway, runner=subprocess.run):
    """Send one bounded diagnostic echo to the local default gateway."""
    if not gateway:
        return {"ok": False, "detail": "no default gateway"}
    try:
        result = runner(
            [PING, "-c", "1", "-W", "3", gateway],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return ({"ok": True, "detail": "reachable"} if result.returncode == 0
                else {"ok": False, "detail": "no reply"})
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "timed out"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "detail": _detail(exc)}


def check_tcp(host, port, connector=socket.create_connection):
    """Try the configured service port without sending credentials or payload."""
    if isinstance(port, bool):
        return {"ok": False, "detail": "invalid port"}
    try:
        port = int(port)
    except (TypeError, ValueError):
        return {"ok": False, "detail": "invalid port"}
    if not 1 <= port <= 65535:
        return {"ok": False, "detail": "invalid port"}
    try:
        sock = connector((host, port), timeout=5)
        sock.close()
        return {"ok": True, "detail": "connected"}
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "detail": _detail(exc)}


def parse_route_get(text):
    """Parse `ip -j route get` without assuming IPv4 or a default route."""
    result = {"interface": "", "source": "", "gateway": ""}
    try:
        routes = json.loads(text)
    except (TypeError, ValueError):
        return result
    if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
        return result
    route = routes[0]
    for output, key in (("interface", "dev"), ("source", "prefsrc"),
                        ("gateway", "gateway")):
        if isinstance(route.get(key), str):
            result[output] = route[key]
    return result


def check_route(address, runner=subprocess.run):
    """Ask the kernel for the route to the actual resolved endpoint."""
    try:
        address = str(ipaddress.ip_address(address))
    except ValueError:
        return {"ok": False, "detail": "invalid route address",
                "interface": "", "address": "", "gateway": ""}
    try:
        result = runner([IP, "-j", "route", "get", address], capture_output=True,
                        text=True, timeout=5, check=False)
        if result.returncode != 0:
            return {"ok": False,
                    "detail": (result.stderr or "no route to target").strip()[:180],
                    "interface": "", "address": "", "gateway": ""}
        route = parse_route_get(result.stdout)
        ok = bool(route["interface"] and route["source"])
        detail = ("%s from %s%s" %
                  (route["interface"], route["source"],
                   " via %s" % route["gateway"] if route["gateway"] else "")) \
            if ok else "no route to target"
        return {"ok": ok, "detail": detail, "interface": route["interface"],
                "address": route["source"], "gateway": route["gateway"]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "route lookup timed out",
                "interface": "", "address": "", "gateway": ""}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "detail": _detail(exc),
                "interface": "", "address": "", "gateway": ""}


def _recv_exact(sock, length):
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def check_protocol(target, rdp_probe=rdpprobe.probe,
                   connector=socket.create_connection):
    """Confirm the expected protocol without sending authentication data."""
    if target.get("via_gateway"):
        return {"ok": True, "detail": "skipped; private RDP service not tested through gateway",
                "skipped": True}
    host, port = target["host"], target["port"]
    if target.get("protocol") == "vnc":
        try:
            sock = connector((host, port), timeout=5)
            try:
                banner = _recv_exact(sock, 12)
            finally:
                sock.close()
            if re.fullmatch(br"RFB \d{3}\.\d{3}\n", banner):
                return {"ok": True, "detail": banner.decode("ascii").strip()}
            return {"ok": False, "detail": "port is open but no VNC banner was received"}
        except OSError as exc:
            return {"ok": False, "detail": _detail(exc)}
    try:
        result = rdp_probe(host, port, timeout=5)
    except OSError as exc:
        return {"ok": False, "detail": _detail(exc)}
    if result.get("selected"):
        return {"ok": True, "detail": "%s — %s" %
                (result["selected"], result.get("description") or "RDP negotiation")}
    return {"ok": False, "detail": result.get("failure") or result.get("error")
            or "RDP negotiation failed"}


def format_network_report(target, local, gateway, ping, dns, tcp,
                          route=None, protocol=None):
    """Create a copyable report containing no usernames, domains, or secrets."""
    name = str(target.get("name") or "Configured server")
    host = str(target.get("host") or "")
    try:
        port = int(target.get("port"))
    except (TypeError, ValueError):
        port = 0

    def outcome(result):
        return "%s — %s" % ("OK" if result.get("ok") else "FAILED",
                             result.get("detail") or "no detail")

    endpoint = "[%s]:%d" % (host, port) if ":" in host else "%s:%d" % (
        host or "not configured", port)
    protocol_name = str(target.get("protocol") or "service").upper()
    lines = [
        "Network diagnostics",
        "Target: %s — %s" % (name, endpoint),
        "Local: %s — %s" % (local.get("interface") or "no active interface",
                             local.get("address") or "no IPv4 address"),
        "Default gateway: %s" % (gateway or "no default gateway"),
        "Gateway ping (informational): %s" % outcome(ping),
        "DNS: %s" % outcome(dns),
        "TCP %d: %s" % (port, outcome(tcp)),
    ]
    if route is not None:
        lines.insert(-1, "Route to target: %s" % outcome(route))
    if protocol is not None:
        lines.append("%s: %s" %
                     (protocol_name,
                     (("SKIPPED" if protocol.get("skipped") else
                       "OK" if protocol.get("ok") else "FAILED") +
                      " — " + (protocol.get("detail") or "no detail"))))
    if target.get("via_gateway"):
        lines.insert(2, "Configured target: %s:%d — private RDP service not tested" %
                     (target.get("configured_host", ""),
                      target.get("configured_port", 3389)))
        lines.insert(3, "Effective endpoint: %s:%d — RD Gateway" % (host, port))
    lines.append("No credentials were sent.")
    return "\n".join(lines)


def run_preflight(target, runner=subprocess.run, connector=socket.create_connection,
                  rdp_probe=rdpprobe.probe):
    """Run the complete on-demand preflight; every operation is time-bounded."""
    try:
        target = normalize_target(target)
    except ValueError as exc:
        invalid = {"ok": False, "detail": "invalid target: %s" % exc}
        return format_network_report(
            {"name": _clean_label(target.get("name")), "host": "", "port": 0},
            {"interface": "", "address": ""}, "", invalid, invalid, invalid,
        )
    local, gateway = check_local_network(runner=runner)
    host, port = target["host"], target["port"]
    dns = check_dns(host, runner=runner)
    address = (dns.get("addresses") or [host])[0] if dns["ok"] else ""
    route = check_route(address, runner=runner) if address \
        else {"ok": False, "detail": "skipped because DNS failed"}
    ping = check_ping(gateway, runner=runner)
    tcp = check_tcp(address, port, connector=connector) if dns["ok"] \
        else {"ok": False, "detail": "skipped because DNS failed"}
    protocol_target = dict(target)
    if address:
        protocol_target["host"] = address
    protocol = check_protocol(protocol_target, connector=connector,
                              rdp_probe=rdp_probe) if tcp["ok"] \
        else {"ok": False, "detail": "skipped because TCP failed", "skipped": True}
    return format_network_report(target, local, gateway, ping, dns, tcp,
                                 route=route, protocol=protocol)
