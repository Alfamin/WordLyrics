"""Small atomic status file for Noctis. Contains song names and reasons, never lyric text."""
import json
import os
import time


class Writer:
    def __init__(self, path, run):
        self.path, self.run, self.last = path, run, 0.0

    def write(self, screen=None, *, final=False, error=""):
        if not self.path or (not final and time.monotonic() - self.last < .8):
            return
        self.last = time.monotonic()
        run = self.run
        stages = [{"name": s.name, "state": s.state, "done": s.done, "total": s.total,
                   "count": s.count, "note": s.note, "current": s.now} for s in run.screen.stages]
        counts = {}
        completed = 0
        for song in run.songs:
            status = song.result.get("status")
            if status:
                completed += 1
                counts[status] = counts.get(status, 0) + 1
        data = {"schema": 1, "updated": time.time(), "pid": os.getpid(), "finished": final,
                "error": error, "total": len(run.songs) or len(run.only_songs or []),
                "completed": completed, "counts": counts, "stages": stages,
                "notice": run.screen.notice, "report": os.path.join(run.run_dir, "report.html")}
        try:
            if os.path.islink(self.path) or os.path.islink(self.path + ".tmp"):
                return
            with open(self.path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(self.path + ".tmp", self.path)
        except OSError:
            pass  # status visibility must never cause a lyric run to fail
