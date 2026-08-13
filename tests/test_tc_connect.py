"""Behaviour of the command-line ThinClient session launcher."""

import importlib.machinery
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


REPO_SCRIPT = (Path(__file__).resolve().parents[1] /
               "overlay/usr/local/bin/tc-connect")
SCRIPT = REPO_SCRIPT if REPO_SCRIPT.is_file() else Path("/usr/local/bin/tc-connect")
REPO_LIBRARY = (Path(__file__).resolve().parents[1] /
                "overlay/usr/local/lib/thinclient")
if REPO_LIBRARY.is_dir():
    sys.path.insert(0, str(REPO_LIBRARY))
LOADER = importlib.machinery.SourceFileLoader("tc_connect", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
tc_connect = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(tc_connect)


class FakeProcess:
    def __init__(self, code):
        self.code = code
        self.stdin = io.BytesIO()

    def wait(self):
        return self.code


class RetryPolicy(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(delete=False)
        handle.close()
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        self.log_path = handle.name
        self.conn = {
            "id": "main", "name": "Main", "host": "server",
            "auto_reconnect": True, "reconnect_delay": 2,
        }

    def run_with_logs(self, outcomes):
        attempts = iter(outcomes)
        env = {"KRB5_CONFIG": "/run/test-krb5.conf"}

        def launch(_argv, **kwargs):
            code, message = next(attempts)
            kwargs["stdout"].write(message)
            kwargs["stdout"].flush()
            return FakeProcess(code)

        with mock.patch.object(tc_connect, "SESSION_LOG", self.log_path), \
                mock.patch.object(tc_connect.tcconfig, "build_command",
                                  return_value=(["xfreerdp3"], None)), \
                mock.patch.object(tc_connect.tcconfig, "prepare_environment",
                                  return_value=env) as prepare, \
                mock.patch.object(tc_connect.subprocess, "Popen",
                                  side_effect=launch) as popen, \
                mock.patch.object(tc_connect.time, "sleep") as sleep:
            code = tc_connect.run_connection(self.conn, {}, once=False)
        return code, env, prepare, popen, sleep

    def test_rejected_credentials_are_never_submitted_again(self):
        code, env, prepare, popen, sleep = self.run_with_logs([
            (131, "[ERROR] ERRCONNECT_LOGON_FAILURE\n"),
        ])

        self.assertEqual(131, code)
        self.assertEqual(1, popen.call_count)
        self.assertIs(env, popen.call_args.kwargs["env"])
        prepare.assert_called_once_with(self.conn)
        sleep.assert_not_called()

    def test_transport_failure_retries_and_preserves_kerberos_environment(self):
        code, env, _prepare, popen, sleep = self.run_with_logs([
            (1, "[ERROR] ERRCONNECT_CONNECT_TRANSPORT_FAILED\n"),
            (0, "connected\n"),
        ])

        self.assertEqual(0, code)
        self.assertEqual(2, popen.call_count)
        self.assertTrue(all(call.kwargs["env"] is env for call in popen.call_args_list))
        sleep.assert_called_once_with(2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
