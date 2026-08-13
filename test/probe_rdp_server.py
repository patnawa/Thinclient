#!/usr/bin/env python3
"""Ask an RDP server what it requires, without attempting to log in.

Sends the X.224 Connection Request that every RDP client begins with and reads
back the negotiation response. That tells you which security protocol the server
will insist on, which is the single most common reason a thin client fails to
connect ("An authentication error has occurred").

    python3 probe_rdp_server.py 192.168.1.10
    python3 probe_rdp_server.py rds.corp.local 3389

No credentials are sent and no logon is attempted, so this leaves no failed
sign-in events behind.
"""

import socket
import struct
import sys

# Protocol flags from [MS-RDPBCGR] 2.2.1.1.1
PROTOCOL_TLS = 0x00000001
PROTOCOL_HYBRID = 0x00000002
PROTOCOL_HYBRID_EX = 0x00000008
REQUESTED_PROTOCOLS = PROTOCOL_TLS | PROTOCOL_HYBRID | PROTOCOL_HYBRID_EX

PROTOCOLS = {
    0x00000000: ("RDP", "legacy RDP security - no TLS"),
    PROTOCOL_TLS: ("TLS", "TLS 1.x, server certificate, no CredSSP"),
    PROTOCOL_HYBRID: ("HYBRID", "NLA / CredSSP - credentials required before the desktop"),
    PROTOCOL_HYBRID_EX: ("HYBRID_EX", "NLA with Early User Authorization"),
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


def connection_request(requested=REQUESTED_PROTOCOLS):
    """Build the X.224 Connection Request that starts an RDP conversation.

    Layout per [MS-RDPBCGR] 2.2.1.1: a TPKT header, an X.224 CR TPDU, then the
    RDP negotiation request. The X.224 length indicator counts every byte after
    itself - CR(1) + DST-REF(2) + SRC-REF(2) + class(1) = 6, plus the
    negotiation request. Getting that byte wrong makes the server reset the
    connection rather than reply.
    """
    neg_req = struct.pack("<BBHI", 0x01, 0x00, 8, requested)
    x224 = struct.pack("!BBHHB", 6 + len(neg_req), 0xE0, 0x0000, 0x0000, 0x00) + neg_req
    return struct.pack("!BBH", 3, 0, 4 + len(x224)) + x224


def parse_response(body):
    """Interpret the X.224 Connection Confirm returned by the server."""
    if len(body) < 8:
        return {"error": "no negotiation response (server may be very old RDP)"}
    neg = body[7:]                      # skip the 7-byte X.224 CC header
    if len(neg) < 8:
        return {"error": "truncated negotiation response"}

    kind, _flags, _length, payload = struct.unpack("<BBHI", neg[:8])
    if kind == 0x02:
        name, description = PROTOCOLS.get(payload, ("UNKNOWN(0x%08X)" % payload, ""))
        return {"selected": name, "description": description, "raw": payload}
    if kind == 0x03:
        return {"failure": FAILURE_CODES.get(payload, "code %d" % payload), "raw": payload}
    return {"error": "unexpected response type 0x%02X" % kind}


def _recv_exact(sock, size):
    """Read up to *size* bytes, tolerating normal short TCP reads."""
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def probe(host, port=3389, timeout=10):
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(connection_request())
        header = _recv_exact(sock, TPKT_HEADER_SIZE)
        if not header:
            return {"error": "server closed the connection without replying"}
        if len(header) < TPKT_HEADER_SIZE:
            return {"error": "truncated TPKT header (%d of %d bytes)" %
                    (len(header), TPKT_HEADER_SIZE)}

        version, reserved, total = struct.unpack("!BBH", header)
        if version != 3 or reserved != 0:
            return {"error": "invalid TPKT header (version %d, reserved %d)" %
                    (version, reserved)}

        minimum = TPKT_HEADER_SIZE + X224_CONFIRM_SIZE
        if total < minimum:
            return {"error": "invalid TPKT length %d (minimum is %d)" %
                    (total, minimum)}

        expected = total - TPKT_HEADER_SIZE
        body = _recv_exact(sock, expected)
        if len(body) < expected:
            return {"error": "truncated TPKT response (%d of %d body bytes)" %
                    (len(body), expected)}
    return parse_response(body)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: probe_rdp_server.py <host> [port]")
    host = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 3389

    print("probing %s:%d" % (host, port))
    try:
        result = probe(host, port)
    except OSError as exc:
        sys.exit("  cannot reach the server: %s" % exc)

    if "error" in result:
        print("  %s" % result["error"])
        return 1
    if "failure" in result:
        print("  negotiation refused: %s" % result["failure"])
        return 1

    print("  server selected : %s" % result["selected"])
    print("  meaning         : %s" % result["description"])
    print()
    if result["selected"].startswith("HYBRID"):
        print("  ThinClient settings:")
        print('    "security": "nla"   (or leave "auto")')
        print("    Credentials must be correct AND the client clock must be within")
        print("    5 minutes of the server, or authentication fails.")
    elif result["selected"] == "TLS":
        print('  ThinClient settings:  "security": "tls"')
    else:
        print('  ThinClient settings:  "security": "rdp"  (unencrypted - LAN only)')
    return 0


if __name__ == "__main__":
    sys.exit(main())
