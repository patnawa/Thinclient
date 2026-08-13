"""Pure contracts for the on-demand network diagnostics.

Every external boundary is injected.  These tests must never inspect the host
running the suite, resolve a real name, or open a real network connection.
"""

import json
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest import mock


REPO_LIBRARY = (Path(__file__).resolve().parents[1] /
                "overlay/usr/local/lib/thinclient")
sys.path.insert(0, str(REPO_LIBRARY if REPO_LIBRARY.is_dir()
                       else "/usr/local/lib/thinclient"))
import networkdiag  # noqa: E402


class TargetNormalization(unittest.TestCase):
    """Turn a saved connection into one safe, explicit probe endpoint."""

    def assert_endpoint(self, connection, **expected):
        self.assertEqual(expected, networkdiag.normalize_target(connection))

    def test_normalizes_dns_ipv4_and_both_ipv6_spellings(self):
        cases = (
            (
                {"name": "Office", "protocol": "RDP",
                 "host": " RDP.Example.COM ", "port": "3390"},
                {"name": "Office", "protocol": "rdp",
                 "host": "rdp.example.com", "port": 3390,
                 "via_gateway": False,
                 "configured_host": "rdp.example.com",
                 "configured_port": 3390},
            ),
            (
                {"name": "IPv4", "protocol": "rdp",
                 "host": "192.0.2.44", "port": 3389},
                {"name": "IPv4", "protocol": "rdp",
                 "host": "192.0.2.44", "port": 3389,
                 "via_gateway": False,
                 "configured_host": "192.0.2.44",
                 "configured_port": 3389},
            ),
            (
                {"name": "IPv6", "protocol": "rdp",
                 "host": "[2001:0db8:0:0::44]", "port": 3389},
                {"name": "IPv6", "protocol": "rdp",
                 "host": "2001:db8::44", "port": 3389,
                 "via_gateway": False,
                 "configured_host": "2001:db8::44",
                 "configured_port": 3389},
            ),
            (
                {"name": "Bare IPv6", "protocol": "rdp",
                 "host": "2001:db8::45", "port": 3389},
                {"name": "Bare IPv6", "protocol": "rdp",
                 "host": "2001:db8::45", "port": 3389,
                 "via_gateway": False,
                 "configured_host": "2001:db8::45",
                 "configured_port": 3389},
            ),
        )
        for connection, expected in cases:
            with self.subTest(host=connection["host"]):
                self.assert_endpoint(connection, **expected)

    def test_idna_hostname_is_ascii_and_case_normalized(self):
        self.assert_endpoint(
            {"name": "International", "protocol": "rdp",
             "host": "BÜCHER.Example", "port": 3389},
            name="International", protocol="rdp",
            host="xn--bcher-kva.example", port=3389,
            via_gateway=False,
            configured_host="xn--bcher-kva.example", configured_port=3389,
        )

    def test_vnc_replaces_an_inherited_rdp_default_port(self):
        self.assert_endpoint(
            {"name": "Lab", "protocol": "vnc", "host": "vnc.example.com",
             "port": 3389, "gateway": "stale-gateway.example.com"},
            name="Lab", protocol="vnc", host="vnc.example.com", port=5900,
            via_gateway=False,
            configured_host="vnc.example.com", configured_port=5900,
        )

    def test_rd_gateway_becomes_the_effective_endpoint(self):
        cases = (
            ("gateway.example.com", "gateway.example.com", 443),
            ("gateway.example.com:4443", "gateway.example.com", 4443),
            ("192.0.2.60:8443", "192.0.2.60", 8443),
            ("[2001:0db8::60]:4443", "2001:db8::60", 4443),
            ("2001:db8::61", "2001:db8::61", 443),
        )
        for configured_gateway, host, port in cases:
            with self.subTest(gateway=configured_gateway):
                self.assert_endpoint(
                    {"name": "Remote", "protocol": "rdp",
                     "host": "private.example.com", "port": 3390,
                     "gateway": configured_gateway},
                    name="Remote", protocol="rdp", host=host, port=port,
                    via_gateway=True,
                    configured_host="private.example.com",
                    configured_port=3390,
                )

    def test_rejects_urls_controls_userinfo_paths_and_malformed_brackets(self):
        invalid_hosts = (
            "https://rdp.example.com", "user@rdp.example.com",
            "rdp.example.com/path", "rdp.example.com?debug=1",
            "rdp.example.com#fragment", "rdp example.com",
            "rdp.example.com\ttail", "rdp.example.com\ntail",
            "rdp.example.com\x00tail", "[2001:db8::1", "[rdp.example.com]",
            "rdp.example.com:3389",
        )
        for host in invalid_hosts:
            with self.subTest(host=repr(host)):
                with self.assertRaises(ValueError):
                    networkdiag.normalize_target({
                        "name": "Unsafe", "protocol": "rdp",
                        "host": host, "port": 3389,
                    })

    def test_rejects_unsafe_gateway_values(self):
        gateways = (
            "https://gateway.example.com", "user@gateway.example.com",
            "gateway.example.com/path", "gateway.example.com:0",
            "gateway.example.com:65536", "gateway.example.com:not-a-port",
            "[2001:db8::1]garbage", "[2001:db8::1]:0",
        )
        for gateway in gateways:
            with self.subTest(gateway=gateway):
                with self.assertRaises(ValueError):
                    networkdiag.normalize_target({
                        "name": "Unsafe gateway", "protocol": "rdp",
                        "host": "private.example.com", "port": 3389,
                        "gateway": gateway,
                    })

    def test_rejects_invalid_ports_and_protocols(self):
        for port in (None, "", 0, -1, 65536, "3389x", True):
            with self.subTest(port=port):
                with self.assertRaises(ValueError):
                    networkdiag.normalize_target({
                        "name": "Bad port", "protocol": "rdp",
                        "host": "rdp.example.com", "port": port,
                    })
        with self.assertRaises(ValueError):
            networkdiag.normalize_target({
                "name": "Bad protocol", "protocol": "ssh",
                "host": "server.example.com", "port": 22,
            })


class LocalNetwork(unittest.TestCase):
    """Turning ``ip -j`` output into one useful local path."""

    def test_uses_the_lowest_metric_default_route_and_its_ipv4_address(self):
        addresses = json.dumps([
            {
                "ifname": "lo",
                "operstate": "UNKNOWN",
                "addr_info": [
                    {"family": "inet", "local": "127.0.0.1", "prefixlen": 8},
                ],
            },
            {
                "ifname": "wlan0",
                "operstate": "UP",
                "addr_info": [
                    {"family": "inet", "local": "10.0.0.20", "prefixlen": 24},
                ],
            },
            {
                "ifname": "enp1s0",
                "operstate": "UP",
                "addr_info": [
                    {
                        "family": "inet6", "local": "fe80::1234",
                        "prefixlen": 64, "scope": "link",
                    },
                    {
                        "family": "inet", "local": "192.168.10.42",
                        "prefixlen": 24, "scope": "global",
                    },
                ],
            },
        ])
        routes = json.dumps([
            {"dst": "192.168.10.0/24", "dev": "enp1s0", "scope": "link"},
            {
                "dst": "default", "gateway": "10.0.0.1",
                "dev": "wlan0", "metric": 600,
            },
            {
                "dst": "default", "gateway": "192.168.10.1",
                "dev": "enp1s0", "metric": 100,
            },
        ])

        local, gateway = networkdiag.parse_local_network(addresses, routes)

        self.assertEqual(
            {"interface": "enp1s0", "address": "192.168.10.42/24"},
            local,
        )
        self.assertEqual("192.168.10.1", gateway)

    def test_no_default_route_keeps_the_available_local_address(self):
        addresses = json.dumps([{
            "ifname": "eth0",
            "operstate": "UP",
            "addr_info": [{
                "family": "inet", "local": "198.51.100.24",
                "prefixlen": 25, "scope": "global",
            }],
        }])

        local, gateway = networkdiag.parse_local_network(addresses, "[]")

        self.assertEqual(
            {"interface": "eth0", "address": "198.51.100.24/25"},
            local,
        )
        self.assertEqual("", gateway)

    def test_ipv6_only_default_route_uses_the_global_address(self):
        addresses = json.dumps([{
            "ifname": "enp1s0",
            "operstate": "UP",
            "addr_info": [
                {
                    "family": "inet6", "local": "fe80::1234",
                    "prefixlen": 64, "scope": "link",
                },
                {
                    "family": "inet6", "local": "2001:db8:10::42",
                    "prefixlen": 64, "scope": "global",
                },
            ],
        }])
        routes = json.dumps([{
            "dst": "default", "gateway": "2001:db8:10::1",
            "dev": "enp1s0", "metric": 100,
        }])

        local, gateway = networkdiag.parse_local_network(addresses, routes)

        self.assertEqual(
            {"interface": "enp1s0", "address": "2001:db8:10::42/64"},
            local,
        )
        self.assertEqual("2001:db8:10::1", gateway)

    def test_malformed_command_output_is_reported_as_unavailable(self):
        for addresses, routes in (("not json", "[]"), ("[]", "{"), ("null", "null")):
            with self.subTest(addresses=addresses, routes=routes):
                self.assertEqual(
                    ({"interface": "", "address": ""}, ""),
                    networkdiag.parse_local_network(addresses, routes),
                )


class RouteCheck(unittest.TestCase):
    """Inspect the kernel route to the resolved endpoint, not just defaults."""

    ROUTE_JSON = json.dumps([{
        "dst": "203.0.113.20", "gateway": "192.168.10.1",
        "dev": "enp1s0", "prefsrc": "192.168.10.42",
        "flags": [], "uid": 1000, "cache": [],
    }])

    def test_parses_route_get_interface_source_and_gateway(self):
        self.assertEqual(
            {"interface": "enp1s0", "source": "192.168.10.42",
             "gateway": "192.168.10.1"},
            networkdiag.parse_route_get(self.ROUTE_JSON),
        )

    def test_direct_route_has_no_gateway(self):
        route = json.dumps([{
            "dst": "192.168.10.60", "dev": "enp1s0",
            "prefsrc": "192.168.10.42", "scope": "link",
        }])
        self.assertEqual(
            {"interface": "enp1s0", "source": "192.168.10.42",
             "gateway": ""},
            networkdiag.parse_route_get(route),
        )

    def test_malformed_or_incomplete_route_output_is_safe(self):
        empty = {"interface": "", "source": "", "gateway": ""}
        for route in ("", "not json", "{}", "null", "[]", "[null]",
                      '[{"dev": 42, "prefsrc": []}]'):
            with self.subTest(route=route):
                self.assertEqual(empty, networkdiag.parse_route_get(route))

    def test_check_route_uses_absolute_ip_and_reports_all_path_fields(self):
        runner = mock.Mock(return_value=types.SimpleNamespace(
            returncode=0, stdout=self.ROUTE_JSON, stderr="",
        ))

        result = networkdiag.check_route("203.0.113.20", runner=runner)

        self.assertTrue(result["ok"])
        self.assertEqual("enp1s0", result["interface"])
        self.assertEqual("192.168.10.42", result["address"])
        self.assertEqual("192.168.10.1", result["gateway"])
        self.assertTrue(result["detail"])
        runner.assert_called_once_with(
            ["/usr/sbin/ip", "-j", "route", "get", "203.0.113.20"],
            capture_output=True, text=True, timeout=5, check=False,
        )

    def test_route_failure_timeout_and_invalid_address_never_escape(self):
        failed = mock.Mock(return_value=types.SimpleNamespace(
            returncode=2, stdout="", stderr="Network is unreachable\n",
        ))
        timed_out = mock.Mock(side_effect=subprocess.TimeoutExpired(
            ["/usr/sbin/ip", "-j", "route", "get", "203.0.113.20"], 5,
        ))
        invalid_runner = mock.Mock()

        failure = networkdiag.check_route("203.0.113.20", runner=failed)
        timeout = networkdiag.check_route("203.0.113.20", runner=timed_out)
        invalid = networkdiag.check_route(
            "203.0.113.20; reboot", runner=invalid_runner
        )

        for result in (failure, timeout, invalid):
            self.assertFalse(result["ok"])
            self.assertEqual("", result["interface"])
            self.assertEqual("", result["address"])
            self.assertEqual("", result["gateway"])
            self.assertTrue(result["detail"])
        self.assertIn("unreachable", failure["detail"].lower())
        self.assertIn("timed out", timeout["detail"].lower())
        invalid_runner.assert_not_called()


class DnsCheck(unittest.TestCase):
    """Name resolution via an injected, bounded ``getent`` invocation."""

    def test_hostname_success_returns_only_unique_numeric_addresses(self):
        runner = mock.Mock(return_value=types.SimpleNamespace(
            returncode=0,
            stdout=(
                "192.0.2.50 STREAM rdp.example.com\n"
                "192.0.2.50 DGRAM  rdp.example.com\n"
                "2001:0db8::50 STREAM rdp.example.com\n"
                "not-an-address RAW rdp.example.com\n"
                "2001:db8::50 DGRAM rdp.example.com\n"
            ),
            stderr="",
        ))

        result = networkdiag.check_dns("rdp.example.com", runner=runner)

        self.assertTrue(result["ok"])
        self.assertEqual(
            ["192.0.2.50", "2001:db8::50"], result["addresses"]
        )
        self.assertIn("192.0.2.50", result["detail"])
        runner.assert_called_once_with(
            ["/usr/bin/getent", "ahosts", "--", "rdp.example.com"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    def test_lookup_failure_and_timeout_are_results_not_exceptions(self):
        failed = mock.Mock(return_value=types.SimpleNamespace(
            returncode=2, stdout="", stderr="",
        ))
        timed_out = mock.Mock(side_effect=subprocess.TimeoutExpired(
            ["/usr/bin/getent", "ahosts", "--", "missing.example.com"], 5,
        ))

        self.assertEqual(
            {"ok": False, "detail": "name not found", "addresses": []},
            networkdiag.check_dns("missing.example.com", runner=failed),
        )
        self.assertEqual(
            {"ok": False, "detail": "lookup timed out", "addresses": []},
            networkdiag.check_dns("missing.example.com", runner=timed_out),
        )

    def test_ip_literal_does_not_need_or_attempt_dns(self):
        for host, canonical in (("203.0.113.7", "203.0.113.7"),
                                ("2001:0db8::7", "2001:db8::7")):
            with self.subTest(host=host):
                runner = mock.Mock()

                result = networkdiag.check_dns(host, runner=runner)

                self.assertEqual(
                    {"ok": True, "detail": "not needed (IP address)",
                     "addresses": [canonical]},
                    result,
                )
                runner.assert_not_called()

    def test_success_exit_with_no_numeric_addresses_is_a_lookup_failure(self):
        runner = mock.Mock(return_value=types.SimpleNamespace(
            returncode=0,
            stdout="malicious.example STREAM rdp.example.com\n",
            stderr="",
        ))

        result = networkdiag.check_dns("rdp.example.com", runner=runner)

        self.assertFalse(result["ok"])
        self.assertEqual([], result["addresses"])
        self.assertTrue(result["detail"])


class GatewayPing(unittest.TestCase):
    """The default gateway probe is bounded and never invokes a shell."""

    def test_success_uses_one_three_second_ping_with_a_five_second_hard_limit(self):
        runner = mock.Mock(return_value=types.SimpleNamespace(
            returncode=0, stdout="64 bytes from 192.168.10.1\n", stderr="",
        ))

        result = networkdiag.check_ping("192.168.10.1", runner=runner)

        self.assertEqual({"ok": True, "detail": "reachable"}, result)
        runner.assert_called_once_with(
            ["/usr/bin/ping", "-c", "1", "-W", "3", "192.168.10.1"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    def test_no_reply_is_a_failure_result(self):
        runner = mock.Mock(return_value=types.SimpleNamespace(
            returncode=1, stdout="", stderr="",
        ))

        self.assertEqual(
            {"ok": False, "detail": "no reply"},
            networkdiag.check_ping("192.168.10.1", runner=runner),
        )

    def test_missing_default_gateway_fails_without_spawning_ping(self):
        runner = mock.Mock()

        result = networkdiag.check_ping("", runner=runner)

        self.assertEqual(
            {"ok": False, "detail": "no default gateway"}, result
        )
        runner.assert_not_called()


class TcpCheck(unittest.TestCase):
    """TCP reachability through an injected five-second connector."""

    def test_success_closes_the_probe_socket(self):
        connection = mock.Mock()
        connector = mock.Mock(return_value=connection)

        result = networkdiag.check_tcp(
            "rdp.example.com", 3389, connector=connector
        )

        self.assertEqual({"ok": True, "detail": "connected"}, result)
        connector.assert_called_once_with(("rdp.example.com", 3389), timeout=5)
        connection.close.assert_called_once_with()

    def test_connection_error_is_returned_for_the_report(self):
        connector = mock.Mock(side_effect=OSError("Connection refused"))

        result = networkdiag.check_tcp(
            "rdp.example.com", 3389, connector=connector
        )

        self.assertEqual(
            {"ok": False, "detail": "Connection refused"}, result
        )

    def test_timeout_is_a_bounded_failure_result(self):
        connector = mock.Mock(side_effect=TimeoutError("timed out"))

        result = networkdiag.check_tcp(
            "203.0.113.20", 3389, connector=connector
        )

        self.assertEqual({"ok": False, "detail": "timed out"}, result)

    def test_invalid_port_does_not_reach_the_socket_boundary(self):
        for port in (0, -1, 65536, "not-a-port", True):
            with self.subTest(port=port):
                connector = mock.Mock()

                result = networkdiag.check_tcp(
                    "203.0.113.20", port, connector=connector
                )

                self.assertFalse(result["ok"])
                self.assertTrue(result["detail"])
                connector.assert_not_called()


class ProtocolCheck(unittest.TestCase):
    """Protocol checks prove the service, without authenticating to it."""

    def test_rdp_hands_the_effective_endpoint_to_the_bounded_probe(self):
        rdp_probe = mock.Mock(return_value={
            "selected": "HYBRID",
            "description": "TLS with CredSSP/NLA",
            "raw": 2,
        })
        connector = mock.Mock()

        result = networkdiag.check_protocol(
            {"protocol": "rdp", "host": "203.0.113.20", "port": 3390,
             "via_gateway": False},
            rdp_probe=rdp_probe, connector=connector,
        )

        self.assertTrue(result["ok"])
        self.assertIn("HYBRID", result["detail"])
        self.assertIn("CredSSP", result["detail"])
        rdp_probe.assert_called_once_with("203.0.113.20", 3390, timeout=5)
        connector.assert_not_called()

    def test_rdp_refusal_malformed_reply_and_network_error_are_failures(self):
        replies = (
            {"failure": "SSL_NOT_ALLOWED_BY_SERVER", "raw": 2},
            {"error": "truncated RDP negotiation response"},
            OSError("Connection refused"),
        )
        for reply in replies:
            with self.subTest(reply=repr(reply)):
                rdp_probe = (mock.Mock(side_effect=reply)
                             if isinstance(reply, Exception)
                             else mock.Mock(return_value=reply))

                result = networkdiag.check_protocol(
                    {"protocol": "rdp", "host": "203.0.113.20",
                     "port": 3389, "via_gateway": False},
                    rdp_probe=rdp_probe, connector=mock.Mock(),
                )

                self.assertFalse(result["ok"])
                self.assertTrue(result["detail"])

    def test_vnc_accepts_a_fragmented_twelve_byte_rfb_banner(self):
        sock = mock.Mock()
        sock.recv.side_effect = [b"RFB 003.", b"008\n"]
        connector = mock.Mock(return_value=sock)
        rdp_probe = mock.Mock()

        result = networkdiag.check_protocol(
            {"protocol": "vnc", "host": "2001:db8::20", "port": 5900,
             "via_gateway": False},
            rdp_probe=rdp_probe, connector=connector,
        )

        self.assertTrue(result["ok"])
        self.assertIn("RFB 003.008", result["detail"])
        connector.assert_called_once_with(("2001:db8::20", 5900), timeout=5)
        self.assertEqual([mock.call(12), mock.call(4)], sock.recv.call_args_list)
        sock.close.assert_called_once_with()
        rdp_probe.assert_not_called()

    def test_vnc_rejects_non_rfb_and_truncated_banners_and_closes(self):
        for chunks in ([b"HTTP/1.1 200"], [b"RFB 003.00", b""]):
            with self.subTest(chunks=chunks):
                sock = mock.Mock()
                sock.recv.side_effect = chunks

                result = networkdiag.check_protocol(
                    {"protocol": "vnc", "host": "203.0.113.21",
                     "port": 5900, "via_gateway": False},
                    rdp_probe=mock.Mock(),
                    connector=mock.Mock(return_value=sock),
                )

                self.assertFalse(result["ok"])
                self.assertTrue(result["detail"])
                sock.close.assert_called_once_with()

    def test_rd_gateway_skips_private_rdp_negotiation(self):
        rdp_probe = mock.Mock()
        connector = mock.Mock()

        result = networkdiag.check_protocol(
            {"protocol": "rdp", "host": "203.0.113.60", "port": 4443,
             "via_gateway": True,
             "configured_host": "private.example.com",
             "configured_port": 3389},
            rdp_probe=rdp_probe, connector=connector,
        )

        self.assertTrue(result["ok"])
        self.assertIn("skipped", result["detail"].lower())
        self.assertIn("gateway", result["detail"].lower())
        rdp_probe.assert_not_called()
        connector.assert_not_called()


class ReportFormatting(unittest.TestCase):
    """The support-facing report is stable and never includes credentials."""

    def test_report_is_exact_and_redacts_all_connection_credentials(self):
        target = {
            "name": "Accounts RDP",
            "protocol": "rdp",
            "host": "rdp.example.com",
            "port": 3389,
            "username": "alice",
            "domain": "CORP",
            "password": "correct horse battery staple",
            "gateway_username": "gateway-admin",
            "gateway_domain": "EDGE",
        }
        local = {"interface": "enp1s0", "address": "192.168.10.42/24"}
        dns = {"ok": True, "detail": "192.0.2.50"}
        tcp = {"ok": False, "detail": "Connection refused"}
        route = {
            "ok": True, "detail": "enp1s0 from 192.168.10.42 via 192.168.10.1",
            "interface": "enp1s0", "address": "192.168.10.42",
            "gateway": "192.168.10.1",
        }
        protocol = {
            "ok": False, "detail": "skipped because TCP failed",
            "skipped": True,
        }

        ping = {"ok": True, "detail": "reachable"}
        report = networkdiag.format_network_report(
            target, local, "192.168.10.1", ping, dns, tcp,
            route=route, protocol=protocol,
        )

        self.assertEqual(
            "Network diagnostics\n"
            "Target: Accounts RDP — rdp.example.com:3389\n"
            "Local: enp1s0 — 192.168.10.42/24\n"
            "Default gateway: 192.168.10.1\n"
            "Gateway ping (informational): OK — reachable\n"
            "DNS: OK — 192.0.2.50\n"
            "Route to target: OK — enp1s0 from 192.168.10.42 via 192.168.10.1\n"
            "TCP 3389: FAILED — Connection refused\n"
            "RDP: SKIPPED — skipped because TCP failed\n"
            "No credentials were sent.",
            report,
        )
        for secret in (
                "alice", "CORP", "correct horse battery staple",
                "gateway-admin", "EDGE"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, report)

    def test_ipv6_endpoints_are_unambiguous_in_the_report(self):
        target = {
            "name": "IPv6 RDP", "protocol": "rdp",
            "host": "2001:db8::44", "port": 3389,
        }
        unavailable = {"ok": False, "detail": "unavailable"}

        report = networkdiag.format_network_report(
            target, {"interface": "", "address": ""}, "", unavailable,
            {"ok": True, "detail": "not needed (IP address)"}, unavailable,
        )

        self.assertIn("IPv6 RDP — [2001:db8::44]:3389", report)


class FullPreflight(unittest.TestCase):
    """All orchestration boundaries are injected and receive sanitized data."""

    ROUTE_JSON = json.dumps([{
        "dst": "192.0.2.50", "gateway": "192.168.10.1",
        "dev": "eth0", "prefsrc": "192.168.10.42",
    }])
    LOCAL_ADDRESSES_JSON = json.dumps([{
        "ifname": "eth0", "operstate": "UP",
        "addr_info": [{
            "family": "inet", "local": "192.168.10.42",
            "prefixlen": 24, "scope": "global",
        }],
    }])
    DEFAULT_ROUTES_JSON = json.dumps([{
        "dst": "default", "gateway": "192.168.10.1",
        "dev": "eth0", "metric": 100,
    }])
    LOCAL_ADDRESS_COMMAND = ["/usr/sbin/ip", "-j", "address", "show"]
    DEFAULT_ROUTE_COMMAND = [
        "/usr/sbin/ip", "-j", "route", "show", "default",
    ]

    @staticmethod
    def completed(stdout="", returncode=0, stderr=""):
        return types.SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr=stderr
        )

    def test_resolves_then_routes_and_probes_the_same_numeric_endpoint(self):
        seen = []

        def run(argv, **kwargs):
            seen.append((argv, kwargs))
            if argv == self.LOCAL_ADDRESS_COMMAND:
                return self.completed(self.LOCAL_ADDRESSES_JSON)
            if argv == self.DEFAULT_ROUTE_COMMAND:
                return self.completed(self.DEFAULT_ROUTES_JSON)
            if argv == ["/usr/bin/getent", "ahosts", "--",
                        "rdp.example.com"]:
                return self.completed(
                    "192.0.2.50 STREAM rdp.example.com\n"
                    "2001:db8::50 STREAM rdp.example.com\n"
                )
            if argv == ["/usr/sbin/ip", "-j", "route", "get",
                        "192.0.2.50"]:
                return self.completed(self.ROUTE_JSON)
            if argv == ["/usr/bin/ping", "-c", "1", "-W", "3",
                        "192.168.10.1"]:
                return self.completed("64 bytes from gateway\n")
            self.fail("unexpected diagnostic command: %r" % (argv,))

        connection = mock.Mock()
        connector = mock.Mock(return_value=connection)
        rdp_probe = mock.Mock(return_value={
            "selected": "HYBRID", "description": "TLS with CredSSP/NLA",
            "raw": 2,
        })
        target = {
            "name": "Accounts RDP", "protocol": "rdp",
            "host": "rdp.example.com", "port": 3389,
        }

        report = networkdiag.run_preflight(
            target, runner=run, connector=connector, rdp_probe=rdp_probe,
        )

        commands = [argv for argv, _kwargs in seen]
        self.assertCountEqual(
            [
                self.LOCAL_ADDRESS_COMMAND,
                self.DEFAULT_ROUTE_COMMAND,
                ["/usr/bin/getent", "ahosts", "--", "rdp.example.com"],
                ["/usr/sbin/ip", "-j", "route", "get", "192.0.2.50"],
                ["/usr/bin/ping", "-c", "1", "-W", "3",
                 "192.168.10.1"],
            ],
            commands,
        )
        for _argv, kwargs in seen:
            self.assertEqual(5, kwargs["timeout"])
            self.assertFalse(kwargs["check"])
            self.assertTrue(kwargs["capture_output"])
            self.assertTrue(kwargs["text"])
        connector.assert_called_once_with(("192.0.2.50", 3389), timeout=5)
        connection.close.assert_called_once_with()
        rdp_probe.assert_called_once_with("192.0.2.50", 3389, timeout=5)
        self.assertIn("Route to target: OK", report)
        self.assertIn("Local: eth0 — 192.168.10.42/24", report)
        self.assertIn("Default gateway: 192.168.10.1", report)
        self.assertIn("Gateway ping (informational): OK — reachable", report)
        self.assertIn("TCP 3389: OK — connected", report)
        self.assertIn("RDP: OK", report)

    def test_ping_failure_is_informational_and_does_not_gate_service_checks(self):
        seen = []

        def run(argv, **kwargs):
            seen.append(argv)
            if argv == self.LOCAL_ADDRESS_COMMAND:
                return self.completed(self.LOCAL_ADDRESSES_JSON)
            if argv == self.DEFAULT_ROUTE_COMMAND:
                return self.completed(self.DEFAULT_ROUTES_JSON)
            if argv[0] == "/usr/bin/getent":
                return self.completed("192.0.2.50 STREAM rdp.example.com\n")
            if argv == ["/usr/sbin/ip", "-j", "route", "get", "192.0.2.50"]:
                return self.completed(self.ROUTE_JSON)
            if argv[0] == "/usr/bin/ping":
                return self.completed(returncode=1)
            self.fail("unexpected diagnostic command: %r" % (argv,))

        tcp_socket = mock.Mock()
        connector = mock.Mock(return_value=tcp_socket)
        rdp_probe = mock.Mock(return_value={
            "selected": "TLS", "description": "TLS security", "raw": 1,
        })
        report = networkdiag.run_preflight(
            {"name": "RDP", "protocol": "rdp",
             "host": "rdp.example.com", "port": 3389},
            runner=run, connector=connector, rdp_probe=rdp_probe,
        )

        self.assertTrue(any(argv[0] == "/usr/bin/ping" for argv in seen))
        connector.assert_called_once_with(("192.0.2.50", 3389), timeout=5)
        rdp_probe.assert_called_once_with("192.0.2.50", 3389, timeout=5)
        self.assertIn("Gateway ping (informational): FAILED — no reply", report)
        self.assertIn("TCP 3389: OK", report)
        self.assertIn("RDP: OK", report)

    def test_rd_gateway_custom_port_is_the_only_public_endpoint_probed(self):
        seen = []

        def run(argv, **_kwargs):
            seen.append(argv)
            if argv == self.LOCAL_ADDRESS_COMMAND:
                return self.completed(self.LOCAL_ADDRESSES_JSON)
            if argv == self.DEFAULT_ROUTE_COMMAND:
                return self.completed(self.DEFAULT_ROUTES_JSON)
            if argv[0] == "/usr/bin/getent":
                return self.completed(
                    "203.0.113.60 STREAM gateway.example.com\n"
                )
            if argv == ["/usr/sbin/ip", "-j", "route", "get",
                        "203.0.113.60"]:
                return self.completed(json.dumps([{
                    "dst": "203.0.113.60", "gateway": "192.168.10.1",
                    "dev": "eth0", "prefsrc": "192.168.10.42",
                }]))
            if argv[0] == "/usr/bin/ping":
                return self.completed()
            self.fail("unexpected diagnostic command: %r" % (argv,))

        tcp_socket = mock.Mock()
        connector = mock.Mock(return_value=tcp_socket)
        rdp_probe = mock.Mock()

        report = networkdiag.run_preflight(
            {"name": "Private RDP", "protocol": "rdp",
             "host": "private.internal", "port": 3389,
             "gateway": "gateway.example.com:4443"},
            runner=run, connector=connector, rdp_probe=rdp_probe,
        )

        self.assertIn(
            ["/usr/bin/getent", "ahosts", "--", "gateway.example.com"],
            seen,
        )
        self.assertIn(
            ["/usr/sbin/ip", "-j", "route", "get", "203.0.113.60"],
            seen,
        )
        connector.assert_called_once_with(("203.0.113.60", 4443), timeout=5)
        rdp_probe.assert_not_called()
        self.assertIn("private.internal:3389", report)
        self.assertIn("gateway.example.com:4443", report)
        self.assertIn("private RDP service not tested", report)

    def test_vnc_uses_effective_5900_for_tcp_and_rfb_protocol_checks(self):
        def run(argv, **_kwargs):
            if argv == self.LOCAL_ADDRESS_COMMAND:
                return self.completed(self.LOCAL_ADDRESSES_JSON)
            if argv == self.DEFAULT_ROUTE_COMMAND:
                return self.completed(self.DEFAULT_ROUTES_JSON)
            if argv == ["/usr/sbin/ip", "-j", "route", "get",
                        "203.0.113.21"]:
                return self.completed(json.dumps([{
                    "dst": "203.0.113.21", "dev": "eth0",
                    "prefsrc": "192.168.10.42",
                }]))
            if argv == ["/usr/bin/ping", "-c", "1", "-W", "3",
                        "192.168.10.1"]:
                return self.completed()
            self.fail("unexpected diagnostic command: %r" % (argv,))

        tcp_socket = mock.Mock()
        vnc_socket = mock.Mock()
        vnc_socket.recv.return_value = b"RFB 003.008\n"
        connector = mock.Mock(side_effect=[tcp_socket, vnc_socket])
        rdp_probe = mock.Mock()

        report = networkdiag.run_preflight(
            {"name": "VNC", "protocol": "vnc",
             "host": "203.0.113.21", "port": 3389},
            runner=run, connector=connector, rdp_probe=rdp_probe,
        )

        self.assertEqual(
            [mock.call(("203.0.113.21", 5900), timeout=5),
             mock.call(("203.0.113.21", 5900), timeout=5)],
            connector.call_args_list,
        )
        rdp_probe.assert_not_called()
        self.assertIn("TCP 5900: OK", report)
        self.assertIn("VNC: OK", report)

    def test_same_lan_target_retains_default_gateway_and_pings_it(self):
        direct_route = json.dumps([{
            "dst": "192.168.10.60", "dev": "eth0",
            "prefsrc": "192.168.10.42", "scope": "link",
        }])
        seen = []

        def run(argv, **_kwargs):
            seen.append(argv)
            if argv == self.LOCAL_ADDRESS_COMMAND:
                return self.completed(self.LOCAL_ADDRESSES_JSON)
            if argv == self.DEFAULT_ROUTE_COMMAND:
                return self.completed(self.DEFAULT_ROUTES_JSON)
            if argv == ["/usr/sbin/ip", "-j", "route", "get",
                        "192.168.10.60"]:
                return self.completed(direct_route)
            if argv == ["/usr/bin/ping", "-c", "1", "-W", "3",
                        "192.168.10.1"]:
                return self.completed()
            self.fail("unexpected diagnostic command: %r" % (argv,))

        tcp_socket = mock.Mock()
        connector = mock.Mock(return_value=tcp_socket)
        rdp_probe = mock.Mock(return_value={
            "selected": "HYBRID", "description": "NLA", "raw": 2,
        })

        report = networkdiag.run_preflight(
            {"name": "Same LAN", "protocol": "rdp",
             "host": "192.168.10.60", "port": 3389},
            runner=run, connector=connector, rdp_probe=rdp_probe,
        )

        self.assertIn(self.LOCAL_ADDRESS_COMMAND, seen)
        self.assertIn(self.DEFAULT_ROUTE_COMMAND, seen)
        self.assertIn(
            ["/usr/bin/ping", "-c", "1", "-W", "3", "192.168.10.1"],
            seen,
        )
        self.assertIn("Local: eth0 — 192.168.10.42/24", report)
        self.assertIn("Default gateway: 192.168.10.1", report)
        self.assertIn("Gateway ping (informational): OK — reachable", report)
        self.assertIn(
            "Route to target: OK — eth0 from 192.168.10.42", report,
        )

    def test_invalid_target_does_not_touch_commands_or_network_sockets(self):
        invalid_runner = mock.Mock()
        invalid_connector = mock.Mock()
        invalid_rdp = mock.Mock()

        invalid_report = networkdiag.run_preflight(
            {"name": "Unsafe", "protocol": "rdp",
             "host": "https://rdp.example.com", "port": 3389},
            runner=invalid_runner, connector=invalid_connector,
            rdp_probe=invalid_rdp,
        )

        self.assertIn("invalid", invalid_report.lower())
        invalid_runner.assert_not_called()
        invalid_connector.assert_not_called()
        invalid_rdp.assert_not_called()

    def test_dns_failure_still_reports_the_independent_local_snapshot(self):
        seen = []

        def failed_dns(argv, **_kwargs):
            seen.append(argv)
            if argv == self.LOCAL_ADDRESS_COMMAND:
                return self.completed(self.LOCAL_ADDRESSES_JSON)
            if argv == self.DEFAULT_ROUTE_COMMAND:
                return self.completed(self.DEFAULT_ROUTES_JSON)
            if argv == ["/usr/bin/getent", "ahosts", "--",
                        "missing.example.com"]:
                return self.completed(returncode=2)
            if argv == ["/usr/bin/ping", "-c", "1", "-W", "3",
                        "192.168.10.1"]:
                return self.completed()
            self.fail("unexpected diagnostic command: %r" % (argv,))

        failed_connector = mock.Mock()
        failed_rdp = mock.Mock()
        failed_report = networkdiag.run_preflight(
            {"name": "Missing", "protocol": "rdp",
             "host": "missing.example.com", "port": 3389},
            runner=failed_dns, connector=failed_connector,
            rdp_probe=failed_rdp,
        )

        self.assertIn("DNS: FAILED", failed_report)
        self.assertIn("skipped", failed_report.lower())
        self.assertIn("Local: eth0 — 192.168.10.42/24", failed_report)
        self.assertIn("Default gateway: 192.168.10.1", failed_report)
        self.assertIn(
            "Gateway ping (informational): OK — reachable", failed_report,
        )
        self.assertIn(self.LOCAL_ADDRESS_COMMAND, seen)
        self.assertIn(self.DEFAULT_ROUTE_COMMAND, seen)
        failed_connector.assert_not_called()
        failed_rdp.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
