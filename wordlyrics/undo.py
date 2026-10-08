"""Take the lyric files this tool wrote out of a music folder again.

Only files that are in the tool's own list AND still have exactly the content the tool wrote are touched.
They are moved into the tool's folder (nothing is deleted), so even an undo can be undone by hand."""
from __future__ import annotations

import csv
import hashlib
import os
from datetime import datetime

from .library import _long_form, _usual_form
from .pipeline import library_home


def plan(home, source):
    """-> (lib_home, [(path, rel)] to move, [(rel, why)] left alone)"""
    lib_home = library_home(home, source)
    listing = os.path.join(lib_home, "all files written.csv")
    move, keep, seen = [], [], set()
    if not os.path.exists(listing):
        return lib_home, move, keep
    from .rerun import records
    retimed={(r["rel"],r["sha256"]) for r in records(home,source)}
    with open(listing, newline="", encoding="utf-8") as fh:
        rows=list(csv.DictReader(fh))
        for row in reversed(rows):
            rel = row["rel"]
            if rel in seen:
                continue
            seen.add(rel)
            path = _usual_form(_long_form(os.path.join(source, rel)))
            from .backup import inside
            if not inside(path,source) or not rel.lower().endswith((".lrc",".elrc")) or os.path.islink(path):
                keep.append((rel,"unsafe ownership record (left alone)"))
                continue
            if not os.path.exists(path):
                keep.append((rel, "no longer there"))
                continue
            with open(path, "rb") as f:
                same = hashlib.sha256(f.read()).hexdigest() == row["sha256"]
            if same:
                if (rel,row["sha256"]) in retimed:
                    keep.append((rel,"retimed lyrics: use Restore previous timing in the menu"))
                else:
                    move.append((path, rel))
            else:
                keep.append((rel, "changed since it was written (left alone)"))
    return lib_home, move, keep


def apply(lib_home, move):
    dest = os.path.join(lib_home, "undone " + datetime.now().strftime("%Y-%m-%d %H.%M.%S.%f"))
    done = 0
    for path, rel in move:
        target = _usual_form(_long_form(os.path.join(dest, rel)))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        try:
            os.replace(path, target)
        except OSError:                      # other drive: copy, check, then take the original away
            with open(path, "rb") as f:
                data = f.read()
            with open(target, "xb") as f:
                f.write(data)
            with open(target, "rb") as f:
                if f.read() != data:
                    continue
            os.remove(path)
        done += 1
    return dest, done
