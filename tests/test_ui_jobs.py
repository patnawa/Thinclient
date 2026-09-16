"""Background work never synchronously blocks or updates a closed dialog."""
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "overlay/usr/local/lib/thinclient"))
import uijobs


class BackgroundJobs(unittest.TestCase):
    def setUp(self):
        self.dispatch = mock.Mock()
        self.job = uijobs.BackgroundJob(self.dispatch)
        self.done = mock.Mock()

    def start_deferred(self, work):
        with mock.patch.object(uijobs.threading, "Thread") as thread:
            self.assertTrue(self.job.start(work, self.done))
            worker = thread.call_args.kwargs["target"]
            thread.return_value.start.assert_called_once()
        return worker

    def deliver(self):
        callback, *args = self.dispatch.call_args.args
        callback(*args)

    def test_work_and_completion_are_deferred(self):
        work = mock.Mock(return_value="done")
        worker = self.start_deferred(work)
        work.assert_not_called()
        self.assertFalse(self.job.start(work, self.done))
        worker()
        self.done.assert_not_called()
        self.deliver()
        self.done.assert_called_once_with("done", "")
        self.assertFalse(self.job.busy)

    def test_closed_dialog_ignores_late_result(self):
        worker = self.start_deferred(lambda: "done")
        self.job.close()
        worker()
        self.deliver()
        self.done.assert_not_called()
        self.assertFalse(self.job.start(lambda: None, self.done))

    def test_timeout_never_echoes_password_in_command(self):
        worker = self.start_deferred(mock.Mock(side_effect=subprocess.TimeoutExpired(
            ["nmcli", "password", "secret-do-not-display"], 90)))
        worker()
        self.deliver()
        message = self.done.call_args.args[1]
        self.assertIn("timed out", message)
        self.assertNotIn("secret", message)
        self.assertFalse(self.job.busy)

    def test_unexpected_errors_are_visible_without_command_details(self):
        worker = self.start_deferred(mock.Mock(side_effect=RuntimeError("secret")))
        worker()
        self.deliver()
        self.assertIn("could not complete", self.done.call_args.args[1])
        self.assertNotIn("secret", self.done.call_args.args[1])

    def test_thread_start_failure_releases_busy_state(self):
        with mock.patch.object(uijobs.threading, "Thread", side_effect=RuntimeError("threads")):
            self.job.start(lambda: None, self.done)
        self.assertFalse(self.job.busy)
        self.done.assert_called_once()


if __name__ == "__main__":
    unittest.main()
