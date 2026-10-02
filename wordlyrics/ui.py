"""The screen: a few lines that are redrawn in place, so the whole state of the run is visible at a glance.
When the output is not a real terminal (a log file), plain lines are printed now and then instead."""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import threading
import time
import unicodedata

WAIT, RUN, DONE, SKIP = "waiting", "running", "done", "skipped"
ESC = chr(27)
ALT_ON, ALT_OFF, HIDE, SHOW = ESC + "[?1049h", ESC + "[?1049l", ESC + "[?25l", ESC + "[?25h"
HOME, CLEAR_LINE, CLEAR_BELOW = ESC + "[H", ESC + "[K", ESC + "[J"


def duration(seconds):
    """Rough, readable time: 'under a minute', '12 min', '2 h 05 min'."""
    seconds = max(0, int(seconds))
    if seconds < 50:
        return "under a minute"
    m = (seconds + 30) // 60
    return "%d min" % m if m < 60 else "%d h %02d min" % (m // 60, m % 60)


def left_text(seconds):
    d = duration(seconds)
    return d + " left" if d.startswith("under") else "about " + d + " left"


def clock(seconds):
    seconds = max(0, int(seconds))
    return "%d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60) if seconds >= 3600 else "%d:%02d" % (seconds // 60, seconds % 60)


def size(n):
    return "%.1f GB" % (n / 1e9) if n >= 1e9 else "%.0f MB" % (n / 1e6)


def _width(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def fit(text, width):
    """Cut text to at most `width` screen columns."""
    out, used = [], 0
    for ch in text:
        w = _width(ch)
        if used + w > width:
            if out and width >= 4:
                while out and used > width - 1:
                    used -= _width(out.pop())
                out.append("…" if _can("…") else ".")
            break
        out.append(ch)
        used += w
    return "".join(out)


def _can(s):
    try:
        s.encode(sys.stdout.encoding or "ascii")
        return True
    except (UnicodeError, LookupError):
        return False


def _enable_ansi():
    if not sys.stdout.isatty() or os.environ.get("TERM") == "dumb":
        return False
    if os.name != "nt":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not k.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        return bool(k.SetConsoleMode(h, mode.value | 0x0004))        # virtual terminal sequences
    except Exception:
        return False


class Stage:
    def __init__(self, name):
        self.name = name
        self.state = WAIT
        self.done = 0.0
        self.total = 0.0
        self.count = ""            # "212 of 680"
        self.now = ""              # what is being worked on
        self.left = None           # seconds, or None when not known yet
        self.note = ""             # shown instead of the time left (when waiting / done)
        self.started = None
        self.took = None

    def start(self):
        if self.state != RUN:
            self.state, self.started = RUN, time.time()

    def finish(self, note=""):
        self.took = time.time() - self.started if self.started else 0
        self.state, self.now, self.left = DONE, "", None
        self.note = note or "done in " + duration(self.took)

    def skip(self, note):
        self.state, self.note = SKIP, note


class Rate:
    """Time left from how fast work has been going lately."""

    def __init__(self, window=180.0):
        self.window = window
        self.points = []            # (time, amount done)

    def add(self, amount):
        now = time.time()
        self.points.append((now, amount))
        while len(self.points) > 2 and now - self.points[0][0] > self.window:
            self.points.pop(0)

    def left(self, remaining):
        if len(self.points) < 2:
            return None
        (t0, a0), (t1, a1) = self.points[0], self.points[-1]
        if a1 <= a0 or t1 - t0 < 2:
            return None
        return remaining / ((a1 - a0) / (t1 - t0))


class Screen:
    def __init__(self, title, plain=False):
        self.title = title
        self.facts = []             # [(label, text)] shown above the stages
        self.stages = []
        self.tally = ""             # one line of counters under the stages
        self.notice = ""            # the latest thing worth telling
        self.live = (not plain) and _enable_ansi()
        self.color = self.live and not os.environ.get("NO_COLOR")
        self.blocks = _can("█░")
        self._restored = False
        self.keys = ""              # one line saying which keys do something (only when keys can be read)
        self.on_key = None          # called with the key that was pressed
        self.can_read_keys = self.live and os.name == "nt" and sys.stdin.isatty()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._last_plain = 0.0
        self.t0 = time.time()

    # ---------------------------------------------------------------- content
    def fact(self, label, text):
        for i, (l, _) in enumerate(self.facts):
            if l == label:
                self.facts[i] = (label, text)
                return
        self.facts.append((label, text))

    def stage(self, name):
        s = Stage(name)
        self.stages.append(s)
        return s

    def say(self, text):
        """Something worth telling once (kept on screen as the latest notice; printed in plain mode)."""
        self.notice = text
        if not self.live:
            print("  " + text, flush=True)

    # ---------------------------------------------------------------- drawing
    def _c(self, code, text):
        return "\x1b[%sm%s\x1b[0m" % (code, text) if self.color else text

    def _bar(self, frac, width=24):
        full = int(round(max(0.0, min(1.0, frac)) * width))
        a, b = ("█", "░") if self.blocks else ("#", "-")
        return a * full + b * (width - full)

    def lines(self):
        cols = max(40, shutil.get_terminal_size((100, 30)).columns - 1)
        out = ["", " " + self._c("1", self.title) + self._c("2", "   running for " + clock(time.time() - self.t0)), ""]
        lab = max([len(l) for l, _ in self.facts] or [0])
        for l, t in self.facts:
            out.append(" " + self._c("2", l.ljust(lab)) + "  " + fit(t, cols - lab - 3))
        out.append("")
        name_w = max([len(s.name) for s in self.stages] or [0])
        for i, s in enumerate(self.stages, 1):
            head = " %d %s  " % (i, s.name.ljust(name_w))
            room = cols - len(head)
            pad = " " * len(head)
            if s.state in (SKIP, WAIT):
                out.append(self._c("2", head + fit(s.note or "waiting", room)))
                continue
            if s.state == DONE:
                tick = "█ " if self.blocks else "ok "
                out.append(self._c("32", head) + self._c("32", tick) + fit("   ".join(x for x in (s.note, s.count) if x), room - len(tick)))
                continue
            # the step that is running: a bar, then what is done and how long is left, then what it is doing now
            frac = min(s.done / s.total, 1.0) if s.total else 0.0
            width = max(10, min(30, room - 6))
            out.append(self._c("1", head) + "%s %3d%%" % (self._bar(frac, width), int(frac * 100)))
            left = left_text(s.left) if s.left is not None else (s.note or ("working out the time left" if frac < 1 else ""))
            out.append(pad + fit("   ".join(x for x in (s.count, left) if x), room))
            if s.now:
                out.append(self._c("2", pad + fit("now: " + s.now, room)))
        out.append("")
        if self.keys:
            out.append(self._c("2", " " + fit(self.keys, cols - 1)))
        if self.tally:
            out.append(" " + fit(self.tally, cols - 1))
        if self.notice:
            out.append(self._c("33", " " + fit(self.notice, cols - 1)))
        out.append("")
        return out

    def draw(self):
        if not self.live:
            now = time.time()
            if now - self._last_plain >= 60:
                self._last_plain = now
                for i, s in enumerate(self.stages, 1):
                    if s.state == RUN:
                        left = ", " + left_text(s.left) if s.left is not None else ""
                        print("  [%s] %s: %s%s" % (clock(now - self.t0), s.name, s.count, left), flush=True)
            return
        with self._lock:
            rows = shutil.get_terminal_size((100, 30)).lines
            lines = self.lines()[: max(rows - 1, 5)]
            # the whole picture is repainted from the top-left corner of its own screen page
            sys.stdout.write(HOME + "".join(ln + CLEAR_LINE + "\n" for ln in lines) + CLEAR_BELOW)
            sys.stdout.flush()

    def _restore(self):
        if self.live and not self._restored:
            self._restored = True
            sys.stdout.write(SHOW + ALT_OFF)
            sys.stdout.flush()

    def start(self):
        if self.live:
            # a separate screen page (like any full-screen program), so the scroll-back stays clean
            sys.stdout.write(ALT_ON + HIDE)
            sys.stdout.flush()
            atexit.register(self._restore)
        else:
            print(self.title, flush=True)

        def loop():
            while not self._stop.wait(0.25 if self.live else 5):
                try:
                    if self.can_read_keys and self.on_key:
                        import msvcrt
                        while msvcrt.kbhit():
                            ch = msvcrt.getwch()
                            if ch in ("\x00", "\xe0"):       # arrow and function keys come as two parts
                                msvcrt.getwch()
                            elif ch == "\x03":
                                import _thread
                                _thread.interrupt_main()     # Ctrl+C read as a key: pass it on as a stop
                            else:
                                self.on_key(ch.lower())
                    self.draw()
                except Exception:
                    pass
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self.live:
            self._restore()
            print("\n".join(self.lines()), flush=True)      # the final picture stays in the normal window
        else:
            for l, t in self.facts:
                print("  %s: %s" % (l, t))
            for s in self.stages:
                print("  %s: %s %s" % (s.name, s.count, s.note), flush=True)
