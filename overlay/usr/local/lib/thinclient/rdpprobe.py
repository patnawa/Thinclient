#!/usr/bin/env python3
"""Credential-free RDP protocol negotiation probe.

This module only performs the initial TPKT/X.224 negotiation.  It does not
send a username, password, CredSSP token, or an RDP logon request.
"""

import socket
import struct


# Protocol flags from [MS-RDPBCGR] 2.2.1.1.1.
PROTOCOL_TLS = 0x00000001
PROTOCOL_HYBRID = 0x00000002
PROTOCOL_HYBRID_EX = 0x00000008
REQUESTED_PROTOCOLS = PROTOCOL_TLS | PROTOCOL_HYBRID | PROTOCOL_HYBRID_EX

PROTOCOLS = {
    0x00000000: ("RDP", "legacy RDP security - no TLS"),
    PROTOCOL_TLS: ("TLS", "TLS 1.x, server certificate, no CredSSP"),
    PROTOCOL_HYBRID: (
        "HYBRID", "NLA / CredSSP - credentials required before the desktop"
    ),
    PROTOCOL_HYBRID_EX: (
        "HYBRID_EX", "NLA with Early User Authorization"
    ),
}

FAILURE_CODES = {
    1: "SSL_REQUIRED_BY_SERVER - the server insists on TLS/NLA",
    2: "SSL_NOT_ALLOWED_BY_SERVER - the server refuses TLS",
    3: "SSL_CERT_NOT_ON_SERVER - the server has no usable certificate",
    4: "INCONSISTENT_FLAGS",
    5: "HYBRID_REQUIRED_BY_SERVER - NLA is mandatory",
    6: "SSL_WITH_USER_AUTH_REQUIRED_BY_SERVER",
}

TPKT_HEADER_SIZE = 4
X224_CONFIRM_SIZE = 7
TPKT_WIRE_MAX = 0xFFFF

# An RDP negotiation response is normally only 19 bytes.  Four KiB leaves
# ample protocol headroom while preventing a hostile endpoint from asking the
# diagnostics UI to buffer an entire maximum-sized TPKT packet.
MAX_TPKT_SIZE = 4 * 1024


def connection_request(requested=REQUESTED_PROTOCOLS):
    """Return the X.224 Connection Request that starts an RDP conversation."""
    if not isinstance(requested, int) or not 0 <= requested <= 0xFFFFFFFF:
        raise ValueError("requested protocols must be a 32-bit unsigned integer")

    neg_req = struct.pack("<BBHI", 0x01, 0x00, 8, requested)
    # The X.224 length indicator counts every byte after itself: six fixed
    # header bytes and the eight-byte RDP negotiation request.
    x224 = struct.pack(
        "!BBHHB", 6 + len(neg_req), 0xE0, 0x0000, 0x0000, 0x00
    ) + neg_req
    return struct.pack("!BBH", 3, 0, TPKT_HEADER_SIZE + len(x224)) + x224


def parse_response(body):
    """Validate and interpret an X.224 Connection Confirm body.

    ``body`` starts immediately after the four-byte TPKT header.  Protocol
    errors are returned as user-facing dictionaries so callers can display a
    useful diagnostic without turning malformed network input into a crash.
    """
    if len(body) < X224_CONFIRM_SIZE:
        return {
            "error": "truncated X.224 Connection Confirm (%d of %d bytes)"
            % (len(body), X224_CONFIRM_SIZE)
        }

    length_indicator = body[0]
    if length_indicator < X224_CONFIRM_SIZE - 1:
        return {
            "error": "invalid X.224 length indicator %d" % length_indicator
        }
    if length_indicator + 1 != len(body):
        return {
            "error": "X.224 length indicator %d does not match %d body bytes"
            % (length_indicator, len(body))
        }
    if body[1] != 0xD0:
        return {
            "error": "unexpected X.224 TPDU type 0x%02X (expected 0xD0)"
            % body[1]
        }

    negotiation = body[X224_CONFIRM_SIZE:]
    if not negotiation:
        return {"error": "no negotiation response (server may be very old RDP)"}
    if len(negotiation) < 4:
        return {"error": "truncated negotiation response header"}

    kind, _flags, declared_length = struct.unpack("<BBH", negotiation[:4])
    if kind not in (0x02, 0x03):
        return {"error": "unexpected response type 0x%02X" % kind}
    if declared_length != 8:
        return {
            "error": "invalid negotiation response length %d (expected 8)"
            % declared_length
        }
    if len(negotiation) != declared_length:
        return {
            "error": "negotiation response length %d does not match %d bytes"
            % (declared_length, len(negotiation))
        }

    payload = struct.unpack("<I", negotiation[4:8])[0]
    if kind == 0x02:
        if payload not in PROTOCOLS:
            return {"error": "unknown selected RDP protocol 0x%08X" % payload,
                    "raw": payload}
        name, description = PROTOCOLS[payload]
        return {"selected": name, "description": description, "raw": payload}

    return {
        "failure": FAILURE_CODES.get(payload, "code %d" % payload),
        "raw": payload,
    }


def _recv_exact(sock, size):
    """Read exactly *size* bytes unless the peer closes the connection."""
    if not isinstance(size, int) or size < 0 or size > MAX_TPKT_SIZE:
        raise ValueError("receive size is outside the bounded TPKT limit")

    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def probe(host, port=3389, timeout=10):
    """Probe an RDP endpoint without attempting authentication or logon."""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(connection_request())
        header = _recv_exact(sock, TPKT_HEADER_SIZE)
        if not header:
            return {"error": "server closed the connection without replying"}
        if len(header) < TPKT_HEADER_SIZE:
            return {
                "error": "truncated TPKT header (%d of %d bytes)"
                % (len(header), TPKT_HEADER_SIZE)
            }

        version, reserved, total = struct.unpack("!BBH", header)
        if version != 3 or reserved != 0:
            return {
                "error": "invalid TPKT header (version %d, reserved %d)"
                % (version, reserved)
            }

        minimum = TPKT_HEADER_SIZE + X224_CONFIRM_SIZE
        if total < minimum:
            return {
                "error": "invalid TPKT length %d (minimum is %d)"
                % (total, minimum)
            }
        if total > TPKT_WIRE_MAX or total > MAX_TPKT_SIZE:
            return {
                "error": "invalid TPKT length %d (maximum accepted is %d)"
                % (total, MAX_TPKT_SIZE)
            }

        expected = total - TPKT_HEADER_SIZE
        body = _recv_exact(sock, expected)
        if len(body) < expected:
            return {
                "error": "truncated TPKT response (%d of %d body bytes)"
                % (len(body), expected)
            }

    return parse_response(body)
