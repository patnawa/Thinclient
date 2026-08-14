"""Pure presentation helpers shared by the ThinClient GTK screens.

Keep parsing and labels here so they can be tested without a display.  The UI
must never infer secrets from logs or expose raw configuration dictionaries.
"""

import os
import re


CACHE_INIT_STATUS = "/run/initramfs/tc-cache-status"
CACHE_SAVE_STATUS = "/run/thinclient/cache-status"
CACHE_PROGRESS_STATUS = "/run/thinclient/cache-progress"
CHANGELOG_LIMIT = 96 * 1024


def clean_text(value, fallback="", limit=160):
    """Return one safe, compact display line."""
    text = " ".join(str(value or "").split())
    return (text or fallback)[:limit]


def changelog_text(path, limit=CHANGELOG_LIMIT):
    """Read the offline release notes with a bounded, useful fallback."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read(max(1, int(limit)) + 1)
    except (OSError, TypeError, ValueError):
        return "Release notes are not available in this image."
    text = text.replace("\x00", "").strip()
    if not text:
        return "Release notes are not available in this image."
    if len(text) > limit:
        text = text[:limit].rstrip() + "\n\n[Older entries omitted]"
    return text


def connection_group(connection):
    """Return a stable friendly group without exposing an endpoint."""
    return clean_text(connection.get("group"), "Connections", 48)


def connection_description(connection):
    """Describe a connection in normal-user language."""
    configured = clean_text(connection.get("description"), "", 100)
    if configured:
        return configured
    protocol = str(connection.get("protocol") or "rdp").lower()
    if protocol == "vnc":
        return "Remote support session"
    if connection.get("app"):
        return "Remote application"
    return "Remote desktop"


def connection_badge(connection):
    protocol = str(connection.get("protocol") or "rdp").lower()
    if protocol == "vnc":
        return "VNC"
    return "RemoteApp" if connection.get("app") else "RDP"


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(4096)
    except OSError:
        return ""


def _key_values(text):
    result = {}
    for line in str(text or "").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if re.fullmatch(r"[a-z0-9_.-]{1,32}", key.strip().lower()):
            result[key.strip().lower()] = clean_text(value, "", 256)
    return result


def cache_status(init_text=None, save_text=None, progress_text=None):
    """Return a non-secret cache state suitable for status and support views."""
    if init_text is None:
        init_text = _read(CACHE_INIT_STATUS)
    if save_text is None:
        save_text = _read(CACHE_SAVE_STATUS)
    if progress_text is None:
        progress_text = _read(CACHE_PROGRESS_STATUS)

    boot = _key_values(init_text)
    progress = _key_values(progress_text)
    saved = _key_values(save_text)
    # Version 1.3 originally wrote a compact legacy success line.
    if not saved and str(save_text or "").startswith("saved "):
        fields = str(save_text).strip().split()
        if len(fields) >= 4:
            saved = {"state": "saved", "profile": fields[1],
                     "sha256": fields[2], "device": fields[3]}

    profile = clean_text(
        progress.get("profile") or saved.get("profile") or boot.get("profile"),
        "", 32,
    )
    profile_label = profile.title() if profile else ""
    suffix = " · %s" % profile_label if profile_label else ""

    if progress.get("state") == "saving":
        try:
            percent = min(100, max(0, int(progress.get("percent", "0"))))
        except ValueError:
            percent = 0
        return {
            "state": "saving", "profile": profile, "percent": percent,
            "summary": "Saving USB boot cache: %d%%%s" % (percent, suffix),
            "detail": "Keep the TCCACHE USB connected until saving finishes.",
        }
    if saved.get("state") == "saved" or saved.get("profile"):
        return {
            "state": "saved", "profile": profile, "percent": 100,
            "summary": "USB boot cache saved and verified%s" % suffix,
            "detail": clean_text(saved.get("device"), "Ready for the next boot."),
        }
    state = boot.get("state", "")
    if state == "hit":
        return {
            "state": "hit", "profile": profile, "percent": 100,
            "summary": "USB boot cache verified%s" % suffix,
            "detail": "The network root download was skipped.",
        }
    if state in ("network", "miss"):
        return {
            "state": "network", "profile": profile, "percent": 0,
            "summary": "Network boot%s" % suffix,
            "detail": "A TCCACHE USB will be populated when available.",
        }
    return {
        "state": "disabled", "profile": profile, "percent": 0,
        "summary": "Network or local boot",
        "detail": "USB root caching is not active for this boot.",
    }


def _outcome(text):
    upper = text.strip().upper()
    if upper.startswith("OK"):
        state = "ok"
    elif upper.startswith("FAILED"):
        state = "failed"
    elif upper.startswith("SKIPPED"):
        state = "skipped"
    else:
        state = "info"
    detail = text.split("—", 1)[1].strip() if "—" in text else text.strip()
    return state, detail


def parse_network_report(report):
    """Turn the copyable diagnostic report into short visual result rows."""
    values = {}
    protocol = None
    for raw in str(report or "").splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key in ("Local", "Default gateway", "Gateway ping (informational)",
                   "DNS", "Route to target") or key.startswith("TCP "):
            values[key] = value
        elif key in ("RDP", "VNC"):
            protocol = (key, value)

    rows = []
    local = values.get("Local", "no active interface — no address")
    local_bad = "no active" in local.lower() or "no ipv" in local.lower()
    rows.append({"label": "Network", "state": "failed" if local_bad else "ok",
                 "detail": local})

    gateway = values.get("Default gateway", "no default gateway")
    ping = values.get("Gateway ping (informational)", "")
    gateway_state = "failed" if gateway.lower().startswith("no ") else "ok"
    ping_state, ping_detail = _outcome(ping) if ping else ("info", "not tested")
    # Ping is informational: a firewall may block ICMP while routing works.
    gateway_detail = gateway + (" · %s" % ping_detail if ping else "")
    rows.append({"label": "Gateway", "state": gateway_state,
                 "detail": gateway_detail, "ping_state": ping_state})

    for label, key in (("DNS", "DNS"), ("Route", "Route to target")):
        state, detail = _outcome(values.get(key, "SKIPPED — not tested"))
        rows.append({"label": label, "state": state, "detail": detail})

    tcp_key = next((key for key in values if key.startswith("TCP ")), "")
    state, detail = _outcome(values.get(tcp_key, "SKIPPED — not tested"))
    port = tcp_key.split(" ", 1)[1] if tcp_key else ""
    rows.append({"label": "Server port%s" % (" " + port if port else ""),
                 "state": state, "detail": detail})

    if protocol:
        state, detail = _outcome(protocol[1])
        rows.append({"label": "%s handshake" % protocol[0],
                     "state": state, "detail": detail})
    return rows


def support_report(info, hardware, hostname, address, cache, last_error=""):
    """Build a credential-free report that can be copied or encoded as QR."""
    lines = [
        "ThinClient support report",
        "Version: %s" % clean_text(info.get("version"), "Unknown"),
        "Image profile: %s" % clean_text(info.get("profile"), "Unknown"),
        "System: %s" % clean_text(info.get("base"), "Unknown"),
        "Kernel: %s" % clean_text(info.get("kernel"), "Unknown"),
        "Device: %s" % clean_text(hostname, "Unknown"),
        "Address: %s" % clean_text(address, "No network"),
        "Boot/cache: %s" % clean_text(cache.get("summary"), "Unknown"),
    ]
    for key in ("Architecture", "Processor", "Memory", "Graphics", "Network"):
        lines.append("%s: %s" % (key, clean_text(hardware.get(key), "Unknown", 300)))
    if last_error:
        lines.append("Last error: %s" % clean_text(last_error, "", 240))
    lines.append("No credentials are included.")
    return "\n".join(lines)
