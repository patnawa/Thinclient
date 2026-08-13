"""The RDP negotiation probe used to check what a server demands.

The expected bytes below come from the protocol specification (MS-RDPBCGR
2.2.1.1), not from the code that builds them. An earlier version of this packet
carried a length indicator of 16 where the spec requires 14, and the server
answered by resetting the connection.
"""

from pathlib import Path
import struct
import sys
import unittest
from unittest import mock

# The build copies this test and the probe into /opt/tests. From a source
# checkout, import the probe from the repository's test/ directory instead.
repo_probe_dir = Path(__file__).resolve().parents[1] / "test"
sys.path.insert(0, str(repo_probe_dir if repo_probe_dir.is_dir() else "/opt/tests"))
import probe_rdp_server  # noqa: E402

rdpprobe = probe_rdp_server.rdpprobe


NEGOTIATION_RESPONSE = (
    bytes.fromhex("0ed00000000000") +
    struct.pack("<BBHI", 0x02, 0x00, 8, probe_rdp_server.PROTOCOL_HYBRID)
)


class FragmentedSocket:
    """Socket double whose recv boundaries are independent of packet boundaries."""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = b""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def sendall(self, payload):
        self.sent += payload

    def recv(self, size):
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if len(chunk) > size:
            self.chunks.insert(0, chunk[size:])
            chunk = chunk[:size]
        return chunk


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


class ProbeResponse(unittest.TestCase):
    """Reading and validating the TPKT response carried over TCP."""

    def run_probe(self, chunks):
        sock = FragmentedSocket(chunks)
        with mock.patch.object(
                rdpprobe.socket, "create_connection", return_value=sock):
            result = probe_rdp_server.probe("rdp.example.com", timeout=2)
        self.assertEqual(probe_rdp_server.connection_request(), sock.sent)
        return result

    def test_fragmented_header_and_body_are_read_to_completion(self):
        packet = struct.pack("!BBH", 3, 0, 4 + len(NEGOTIATION_RESPONSE))
        packet += NEGOTIATION_RESPONSE

        result = self.run_probe([
            packet[:1], packet[1:3], packet[3:5], packet[5:9], packet[9:]
        ])

        self.assertEqual("HYBRID", result["selected"])

    def test_connection_closed_during_header_is_reported_as_truncated(self):
        result = self.run_probe([b"\x03\x00"])

        self.assertIn("truncated TPKT header", result["error"])

    def test_invalid_tpkt_version_is_rejected(self):
        result = self.run_probe([
            struct.pack("!BBH", 4, 0, 4 + len(NEGOTIATION_RESPONSE))
        ])

        self.assertIn("invalid TPKT header", result["error"])

    def test_nonzero_tpkt_reserved_byte_is_rejected(self):
        result = self.run_probe([
            struct.pack("!BBH", 3, 1, 4 + len(NEGOTIATION_RESPONSE))
        ])

        self.assertIn("invalid TPKT header", result["error"])

    def test_invalid_tpkt_length_is_rejected(self):
        result = self.run_probe([struct.pack("!BBH", 3, 0, 10)])

        self.assertIn("invalid TPKT length", result["error"])

    def test_oversized_tpkt_is_rejected_without_reading_its_body(self):
        result = self.run_probe([
            struct.pack("!BBH", 3, 0, rdpprobe.MAX_TPKT_SIZE + 1)
        ])

        self.assertIn("maximum accepted", result["error"])

    def test_connection_closed_during_body_is_reported_as_truncated(self):
        header = struct.pack("!BBH", 3, 0, 4 + len(NEGOTIATION_RESPONSE))
        result = self.run_probe([header, NEGOTIATION_RESPONSE[:8]])

        self.assertIn("truncated TPKT response", result["error"])


class ResponseValidation(unittest.TestCase):
    """Reject packets that look similar to a negotiation but are not valid."""

    def test_x224_connection_confirm_type_is_required(self):
        body = bytearray(NEGOTIATION_RESPONSE)
        body[1] = 0xE0

        result = rdpprobe.parse_response(bytes(body))

        self.assertIn("expected 0xD0", result["error"])

    def test_x224_length_indicator_must_match_packet(self):
        body = bytearray(NEGOTIATION_RESPONSE)
        body[0] -= 1

        result = rdpprobe.parse_response(bytes(body))

        self.assertIn("does not match", result["error"])

    def test_truncated_negotiation_header_is_rejected(self):
        body = bytes.fromhex("09d00000000000") + b"\x02\x00\x08"

        result = rdpprobe.parse_response(body)

        self.assertIn("truncated negotiation response header", result["error"])

    def test_unexpected_negotiation_type_is_rejected(self):
        body = bytes.fromhex("0ed00000000000") + struct.pack(
            "<BBHI", 0x01, 0, 8, rdpprobe.PROTOCOL_TLS
        )

        result = rdpprobe.parse_response(body)

        self.assertIn("unexpected response type", result["error"])

    def test_declared_negotiation_length_must_be_eight(self):
        body = bytes.fromhex("0ed00000000000") + struct.pack(
            "<BBHI", 0x02, 0, 9, rdpprobe.PROTOCOL_TLS
        )

        result = rdpprobe.parse_response(body)

        self.assertIn("expected 8", result["error"])

    def test_declared_negotiation_length_must_match_available_bytes(self):
        body = bytes.fromhex("0fd00000000000") + struct.pack(
            "<BBHI", 0x02, 0, 8, rdpprobe.PROTOCOL_TLS
        ) + b"\x00"

        result = rdpprobe.parse_response(body)

        self.assertIn("does not match", result["error"])

    def test_failure_response_is_decoded(self):
        body = bytes.fromhex("0ed00000000000") + struct.pack(
            "<BBHI", 0x03, 0, 8, 5
        )

        result = rdpprobe.parse_response(body)

        self.assertIn("HYBRID_REQUIRED", result["failure"])

    def test_unknown_selected_protocol_is_rejected(self):
        body = bytes.fromhex("0ed00000000000") + struct.pack(
            "<BBHI", 0x02, 0, 8, 0x40000000
        )

        result = rdpprobe.parse_response(body)

        self.assertIn("unknown selected RDP protocol", result["error"])
        self.assertNotIn("selected", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
