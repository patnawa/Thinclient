"""One bounded background operation per dialog; widgets stay on the GTK thread."""
import subprocess
import threading


class BackgroundJob:
    def __init__(self, dispatch):
        self.dispatch = dispatch
        self.busy = False
        self.closed = False

    def close(self):
        # Closing the UI does not undo an already submitted network change.
        self.closed = True

    def start(self, work, done):
        if self.busy or self.closed:
            return False
        self.busy = True

        def finish(result, error):
            self.busy = False
            if not self.closed:
                done(result, error)
            return False

        def worker():
            try:
                result, error = work(), ""
            except subprocess.TimeoutExpired:
                result, error = None, "The operation timed out. Check the device and try again."
            except Exception:  # Never echo a subprocess command containing a password.
                result, error = None, "The operation could not complete. Check the device and try again."
            self.dispatch(finish, result, error)

        try:
            threading.Thread(target=worker, daemon=True, name="thinclient-ui-job").start()
        except RuntimeError:
            self.busy = False
            done(None, "Could not start the operation. Try again.")
        return True
