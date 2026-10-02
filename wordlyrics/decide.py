"""What the models heard + the lyrics to try -> what should be written for one song.

Pure calculation, no files: it runs in a helper process so that it never slows the listening down."""
from __future__ import annotations

from . import timing as T


def init():
    """Start-up of the helper process: Ctrl+C is handled by the main program, and the helper keeps out of the way."""
    import multiprocessing
    import os
    import signal
    import threading
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    low_priority()
    main = multiprocessing.parent_process()

    def leave_with_main():                  # the main program was ended hard (window closed, task manager):
        main.join()                         # do not stay behind
        os._exit(0)
    if main is not None:
        threading.Thread(target=leave_with_main, daemon=True).start()


def low_priority(idle=False):
    """Let everything else on the computer go first."""
    import os
    try:
        if os.name == "nt":
            import ctypes
            k = ctypes.windll.kernel32
            k.SetPriorityClass(k.GetCurrentProcess(), 0x00000040 if idle else 0x00004000)   # idle / below normal
        else:
            os.nice(15 if idle else 5)
    except Exception:
        pass


def judge(feat, attempts):
    """attempts: [(mode 'line'|'plain', lyrics text, whose 'yours'|'found')], best first.
    -> {"timed": info with the .elrc text, or None,
        "keep_lines": (text, info) for found line-timed lyrics that are right but could not be word-timed, or None,
        "last": info of the last attempt that did not work, or None}"""
    last = keep = None
    for mode, text, whose in attempts:
        fits, ratio = T.belongs(feat, text, plain=(mode == "plain"))
        info = {"mode": mode, "lyrics": whose, "fits_recording": {True: "yes", False: "no", None: "cannot tell"}[fits], "fit_ratio": ratio}
        if whose == "found" and fits is False:
            # lyrics from the internet that do not belong to this recording are never written
            last = dict(info, status="rejected", reason="the lyrics found online do not match what is sung")
            continue
        if mode == "line":
            elrc, rows, st, problems = T.process(feat, text)
            share = st["lines_aligned"] / st["lines"] if st.get("lines") else 0.0
            refused = problems[0] if problems else None if (elrc is not None and share >= T.MIN_LINE_SHARE) else \
                "only %d of %d lines could be heard well enough" % (st.get("lines_aligned", 0), st.get("lines", 0))
            grade = "good" if share >= 0.95 else "fair" if share >= 0.85 else "poor"
        else:
            elrc, rows, st, problems = T.process_plain(feat, text)
            refused = (problems[0] if problems else "no timing fits") if (elrc is None or problems) else T.plain_gate(st)
            n = max(st.get("lines") or 0, 1)
            grade = "good" if st.get("lines_unsure", n) / n <= 0.10 else "fair"
        info.update(lines=st.get("lines"), lines_word_timed=st.get("lines_aligned"), lines_not_heard=st.get("lines_unheard"),
                    words=st.get("words"), confidence=grade)
        if refused:
            last = dict(info, status="not_timed", reason=refused, confidence="")
            if whose == "found" and mode == "line" and elrc is not None and not problems and fits:
                keep = (text, dict(last))                  # right lyrics with line times: still worth having
            continue
        return {"timed": dict(info, status="timed", elrc=elrc), "keep_lines": None, "last": None}
    return {"timed": None, "keep_lines": keep, "last": last}
