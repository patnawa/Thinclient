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

from pathlib import Path
import sys

# The script runs both from a source checkout and from /opt/tests in an image.
repo_module_dir = (
    Path(__file__).resolve().parents[1] / "overlay/usr/local/lib/thinclient"
)
installed_module_dir = Path("/usr/local/lib/thinclient")
for module_dir in (repo_module_dir, installed_module_dir):
    if (module_dir / "rdpprobe.py").is_file():
        sys.path.insert(0, str(module_dir))
        break

import rdpprobe  # noqa: E402

# Re-export the original public surface so existing users importing this
# diagnostic script keep working while the implementation lives in the image.
PROTOCOL_TLS = rdpprobe.PROTOCOL_TLS
PROTOCOL_HYBRID = rdpprobe.PROTOCOL_HYBRID
PROTOCOL_HYBRID_EX = rdpprobe.PROTOCOL_HYBRID_EX
REQUESTED_PROTOCOLS = rdpprobe.REQUESTED_PROTOCOLS
PROTOCOLS = rdpprobe.PROTOCOLS
FAILURE_CODES = rdpprobe.FAILURE_CODES
TPKT_HEADER_SIZE = rdpprobe.TPKT_HEADER_SIZE
X224_CONFIRM_SIZE = rdpprobe.X224_CONFIRM_SIZE
MAX_TPKT_SIZE = rdpprobe.MAX_TPKT_SIZE
connection_request = rdpprobe.connection_request
parse_response = rdpprobe.parse_response
probe = rdpprobe.probe


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
