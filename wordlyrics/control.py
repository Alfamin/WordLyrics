"""Pause and Cancel from the Noctis plugin: a tiny file that the plugin writes and this reads twice a second.

  pause   the models rest after the step they are in (the same pause as the P key)
  run     work on; also what a missing or unreadable file means
  stop    stop like Ctrl+C: finish the song being timed, check the folder, write the report

Only a run that was started with --control FILE (by the plugin) looks at any file."""
from __future__ import annotations

import threading

POLL_S = 0.5
UNCLEAR_LIMIT = 3          # polls in a row that could not be made sense of before the run simply goes on


class Control:
    def __init__(self, path, run, changed=None, interval=POLL_S):
        self.path = path
        self.run = run
        self.changed = changed             # called after every change, e.g. to write the status file at once
        self.interval = interval
        self.state = "run"
        self.unclear = 0
        self._done = threading.Event()
        self._thread = None

    def read(self):
        """-> "pause" | "run" | "stop", or None when the file is there but cannot be read or understood."""
        try:
            with open(self.path, "rb") as fh:
                raw = fh.read(64)
        except FileNotFoundError:
            return "run"
        except OSError:
            return None
        word = raw.decode("utf-8-sig", "replace").strip().lower()
        return word if word in ("pause", "run", "stop") else None

    def poll(self):
        """One look at the file. A word that cannot be understood is only believed to mean "run" after a few
        looks in a row, so that a file being replaced at this very moment never lets a paused run slip."""
        if self.state == "stop":
            return
        wanted = self.read()
        if wanted is None:
            self.unclear += 1
            if self.unclear < UNCLEAR_LIMIT:
                return
            wanted = "run"
        else:
            self.unclear = 0
        if wanted == self.state:
            return
        self.state = wanted
        if wanted == "stop":
            self.run.screen.say("Stopping: the song being timed is finished first.")
            self.run.request_stop()
        elif wanted == "pause":
            self.run.set_speed(pause=True)
            self.run.screen.say("Paused from the Noctis window. The models rest after the step they are in.")
        else:
            self.run.set_speed(pause=False)
            self.run.screen.say("Working again.")
        if self.changed:
            self.changed()

    def _loop(self):
        while not self._done.is_set():
            try:
                self.poll()
            except Exception:                  # looking at the file must never stop the work itself
                pass
            if self.state == "stop" or self._done.wait(self.interval):
                return

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def close(self):
        self._done.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
