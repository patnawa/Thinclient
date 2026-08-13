"""The RDP negotiation probe used to check what a server demands.

The expected bytes below come from the protocol specification (MS-RDPBCGR
2.2.1.1), not from the code that builds them. An earlier version of this packet
carried a length indicator of 16 where the spec requires 14, and the server
answered by resetting the connection.
"""

import sys
import unittest

sys.path.insert(0, "/opt/tests")
import probe_rdp_server  # noqa: E402


class NegotiationRequest(unittest.TestCase):
    """The X.224 Connection Request that opens every RDP conversation."""

    def test_connection_request_matches_the_protocol_spec(self):
        #   03 00 00 13        TPKT: version 3, reserved 0, total length 19
        #   0e                 X.224 length indicator: 6 header bytes + 8 of
        #                      negotiation request, counted after this byte
        #   e0                 CR TPDU
        #   00 00  00 00  00   DST-REF, SRC-REF, class 0
        #   01 00  08 00       RDP_NEG_REQ: type 1, flags 0, length 8 (LE)
        #   0b 00 00 00        requestedProtocols = TLS|HYBRID|HYBRID_EX (LE)
        expected = bytes.fromhex("03000013" "0ee00000000000" "01000800" "0b000000")

        self.assertEqual(expected, probe_rdp_server.connection_request())


if __name__ == "__main__":
    unittest.main(verbosity=2)
