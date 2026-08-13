"""Behaviour of the ThinClient configuration core.

These run against the tcconfig module as installed in the built image, using
stdlib unittest so they can run on the build host or on a client itself.

Expected command lines are known-good literals verified against the FreeRDP
binary that ships in the image (see build/rdpcheck.sh, which cross-checks every
option we emit against `xfreerdp3 --help`). They are never recomputed the way
build_command computes them, or the test could never disagree with the code.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, "/usr/local/lib/thinclient")
import tcconfig  # noqa: E402


def connection(**overrides):
    """A connection with everything off, so each test states what it needs."""
    conn = dict(tcconfig.CONNECTION_DEFAULTS)
    conn.update({
        "id": "test", "name": "Test", "host": "server.example.com", "port": 3389,
        "audio_out": False, "audio_in": False, "redirect_clipboard": False,
        "redirect_usb_storage": False, "redirect_usb_devices": False,
        "redirect_smartcard": False, "redirect_printers": False,
        "auto_reconnect": False, "extra_args": [],
    })
    conn.update(overrides)
    return conn


def target_of(argv):
    """The /v: argument, which is what identifies the server."""
    for arg in argv:
        if arg.startswith("/v:"):
            return arg
    return None


class TargetAddress(unittest.TestCase):
    """How a server address becomes FreeRDP's /v: argument."""

    def test_ipv6_address_is_bracketed(self):
        # FreeRDP rejects a bare IPv6 literal outright:
        #   "Command line parsing failed at 'v' value '2001:db8::1'"
        # because it cannot tell the address colons from the port separator.
        # Brackets are the documented form and the binary accepts them.
        argv, _ = tcconfig.build_command(
            connection(host="2001:db8::1"), {"keyboard_layout": ""}
        )
        self.assertEqual("/v:[2001:db8::1]", target_of(argv))


class SessionFailure(unittest.TestCase):
    """What the user is told when a session ends badly, and whether we retry it."""

    def log_containing(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_missing_credentials_are_reported_as_missing_credentials(self):
        # Regression: a client on Hyper-V reported only "the connection was
        # cancelled". FreeRDP emits that when it needs credentials and has no
        # terminal to ask on - i.e. the fields were left empty - so the message
        # has to name the actual problem or nobody can act on it.
        path = self.log_containing(
            "[ERROR][com.freerdp.core] - [nla_client_setup_identity]: "
            "ERRCONNECT_CONNECT_CANCELLED [0x0002000B]\n"
        )
        failure = tcconfig.explain_failure(path, 1)
        self.assertIn("password", failure.message.lower())
        self.assertFalse(failure.retryable)

    def test_rejected_credentials_are_not_retried(self):
        # Reconnecting with the same rejected password cannot succeed, and on a
        # domain it walks the account towards a lockout. The caller must be able
        # to ask this directly rather than pattern-matching the English message.
        path = self.log_containing(
            "[13:35:11:406] [1:1] [ERROR][com.freerdp.core] - "
            "[rdp_client_connect]: ERRCONNECT_LOGON_FAILURE [0x0002000C]\n"
        )
        failure = tcconfig.explain_failure(path, 131)
        self.assertFalse(failure.retryable)


class VncConnections(unittest.TestCase):
    """Connections that speak VNC rather than RDP."""

    def test_a_vnc_connection_launches_the_vnc_viewer_on_the_right_port(self):
        # TigerVNC's own usage says "[host][::port]" - a single colon means a
        # display number, so 10.0.0.5:5901 would be display 5901, not port 5901.
        argv, _ = tcconfig.build_command(
            connection(protocol="vnc", host="10.0.0.5", port=5901),
            {"keyboard_layout": ""},
        )

        self.assertIn("vncviewer", argv[0])
        self.assertIn("10.0.0.5::5901", argv)
        self.assertIn("-FullScreen", argv)


class StdinCredentials(unittest.TestCase):
    """Handing FreeRDP a password without putting it on the command line."""

    def test_a_blank_domain_is_answered_before_the_password(self):
        # FreeRDP prompts for every credential it was not given, in the order
        # username, domain, password, and reads the answers from stdin. An
        # empty domain still counts as missing, so a lone password line gets
        # consumed as the domain and the password prompt then hits end of
        # input - which aborts the connection with ERRCONNECT_CONNECT_CANCELLED
        # and no explanation. Verified against the shipped FreeRDP: with a
        # leading blank line the connection reaches the server and the
        # credentials are properly evaluated.
        argv, stdin_text = tcconfig.build_command(
            connection(username="msc", domain=""),
            {"keyboard_layout": ""},
            password="secret",
        )

        self.assertIn("/from-stdin", argv)
        self.assertEqual("\nsecret\n", stdin_text)
        self.assertFalse([a for a in argv if a.startswith("/p:")],
                         "the password must not appear on the command line")


class ExtraArguments(unittest.TestCase):
    """Extra FreeRDP arguments an administrator types into Settings."""

    def test_a_quoted_argument_containing_spaces_stays_one_argument(self):
        # The documented printer workaround has to name a Windows driver, and
        # those names contain spaces. Splitting on whitespace would hand
        # FreeRDP six broken fragments instead of one /printer option.
        # There is no shell involved, so the quotes are removed and the value
        # arrives as a single argv element.
        typed = '/printer:HP,"HP Color LaserJet 2800 Series PS" -themes'

        self.assertEqual(
            ["/printer:HP,HP Color LaserJet 2800 Series PS", "-themes"],
            tcconfig.parse_extra_args(typed),
        )


class ConfigLayering(unittest.TestCase):
    """Factory defaults, boot media, central config and this session's edits."""

    def layer(self, payload):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(payload, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_a_later_layer_overrides_only_the_keys_it_sets(self):
        # Central management usually ships one or two settings and expects the
        # rest of the device configuration to survive, so device layers merge
        # key by key rather than replacing wholesale.
        factory = self.layer({"device": {"timezone": "UTC", "keyboard_layout": "th"}})
        central = self.layer({"device": {"timezone": "Asia/Bangkok"}})

        cfg = tcconfig.load(layers=(factory, central))

        self.assertEqual("Asia/Bangkok", cfg["device"]["timezone"])
        self.assertEqual("th", cfg["device"]["keyboard_layout"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
