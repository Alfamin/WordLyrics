"""The report of one run: a page to read (report.html), a table of every song (songs.csv) and a short text summary."""
from __future__ import annotations

import csv
import html
import os
import urllib.parse
from datetime import datetime

from . import __version__
from . import library as L
from .ui import duration, size

GROUPS = [
    ("timed", "Word-by-word lyrics written", "These songs now have a word-timed lyrics file next to them."),
    ("lines_only", "Line-timed lyrics written (no word timing)", "Lyrics were found and belong to the song, but too few lines could be heard clearly enough for word timing. The song got an ordinary line-timed .lrc file."),
    ("skipped", "Left alone", "Nothing to do for these songs."),
    ("not_timed", "Lyrics present, but could not be timed well enough", "Nothing was written for these. Their existing lyrics are untouched."),
    ("no_lyrics", "No usable lyrics found", "No usable lyric source was found for this run. Existing lyric files, if any, were preserved."),
    ("network_error", "Lyric provider unavailable", "The search could not be completed because a provider could not be reached. Check the connection diagnostics and retry these songs."),
    ("rejected", "Online lyrics could not be confirmed", "The model's match check did not confirm these lyrics for this recording; nothing was written."),
    ("failed", "Could not be read", "The audio of these files could not be used."),
    ("not_reached", "Not reached", "The run was stopped before these songs. Run again to continue; finished songs are skipped."),
]
ORIGIN = {("line", "yours"): "your line-timed lyrics", ("plain", "yours"): "your plain lyrics (lines placed by the model)",
          ("line", "found"): "line-timed lyrics found online", ("plain", "found"): "plain lyrics found online (lines placed by the model)"}


def detail(s):
    r = s.result
    bits = []
    if r.get("mode"):
        bits.append(ORIGIN.get((r["mode"], r.get("lyrics")), ""))
    if r.get("lines"):
        bits.append("%s of %s lines word-timed" % (r.get("lines_word_timed") or 0, r["lines"]))
        if r.get("lines_not_heard"):
            bits.append("%d not heard" % r["lines_not_heard"])
    if r.get("confidence"):
        bits.append("confidence " + r["confidence"])
    if r.get("reason"):
        bits.append(r["reason"])
    if r.get("timing_note"):
        bits.append(r["timing_note"])
    if r.get("files"):
        bits.append("wrote " + " + ".join(r["files"]))
    return "; ".join(b for b in bits if b)


def summary_lines(run):
    c = {}
    for s in run.songs:
        c[s.result.get("status", "not_reached")] = c.get(s.result.get("status", "not_reached"), 0) + 1
    already = sum(1 for s in run.songs if s.result.get("status") == "skipped" and "already has word-by-word lyrics" in s.result.get("reason", ""))
    took = (datetime.now() - run.started).total_seconds()
    out = ["%d songs in %s" % (len(run.songs), run.source), ""]
    from .outcome import summarize
    out.insert(0, summarize(run)["message"])
    out.append("  %5d  now have word-by-word lyrics from this run" % c.get("timed", 0))
    out.append("  %5d  already had word-by-word lyrics (left alone)" % already)
    for key, label in (("lines_only", "got line-timed lyrics only"), ("not_timed", "have lyrics that could not be timed well enough (nothing written)"),
                       ("no_lyrics", "had no usable lyric source found (existing files preserved)"), ("rejected", "had online lyrics rejected (did not match the recording)"),
                       ("network_error", "could not finish lyric search (connection/provider error)"),
                       ("failed", "could not be read"), ("not_reached", "were not reached (run stopped early)")):
        if c.get(key):
            out.append("  %5d  %s" % (c[key], label))
    other = c.get("skipped", 0) - already
    if other:
        out.append("  %5d  left alone for another reason" % other)
    out.append("")
    out.append("Lyric files written: %d   Time taken: %s" % (len(run.written), duration(took)))
    if run.backup_rec:
        out.append("Backup copy: %s" % run.backup_rec["path"])
    if run.check:
        k = run.check
        out.append("Check of the music folder: " + ("original audio preserved; %d lyric files explicitly retimed; other new files are this run's lyrics." % len(k.get("retimed",[]))
                                                    if k["clean"] else "%d missing, %d changed, %d new files not from this run - see the report."
                                                    % (len(k["missing"]), len(k["changed"]), len(k["new_not_by_this_run"]))))
    for p in run.problems:
        out += ["", "Note: " + p]
    return out


CSS = """
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1f;--dim:#6b6b70;--line:#e4e2dd;--good:#1f7a4d;--warn:#a05a00;--bad:#b3261e;--accent:#3b5bdb}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1e1e22;--ink:#ececf0;--dim:#9a9aa3;--line:#2e2e34;--good:#5fd39a;--warn:#f0b35a;--bad:#ff8a80;--accent:#8fa6ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Segoe UI",system-ui,sans-serif}
main{max-width:1000px;margin:0 auto;padding:32px 16px 64px}h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:0}
.dim{color:var(--dim)}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:24px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}.card b{display:block;font-size:26px;font-variant-numeric:tabular-nums}
.box{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:16px 0}
.ok{border-left:4px solid var(--good)}.attn{border-left:4px solid var(--warn)}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:12px 0;padding:0 18px}
summary{cursor:pointer;padding:14px 0;font-weight:600;font-size:16px}summary span{color:var(--dim);font-weight:400}
table{width:100%;border-collapse:collapse;margin:0 0 14px;font-size:14px}td{padding:6px 8px 6px 0;border-top:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere}
td:first-child{width:46%}code{background:var(--bg);padding:1px 5px;border-radius:4px;font-size:13px;overflow-wrap:anywhere}p{margin:8px 0}
"""


def write(run):
    """-> path of report.html"""
    os.makedirs(run.run_dir, exist_ok=True)
    e = html.escape
    by = {}
    for s in run.songs:
        by.setdefault(s.result.get("status", "not_reached"), []).append(s)
    with open(os.path.join(run.run_dir, "songs.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["song", "result", "lyrics before", "lyrics used", "lyrics came from", "lines", "lines word-timed", "lines not heard",
                    "confidence", "lyrics fit the recording", "files written", "note", "artist", "title", "length (s)"])
        for s in run.songs:
            r = s.result
            w.writerow([s.rel, r.get("status", "not_reached"), L.TIER_NAME[s.previous_tier if s.previous_tier is not None else s.tier], ORIGIN.get((r.get("mode"), r.get("lyrics")), ""),
                        r.get("lyrics_from", s.lyrics_from), r.get("lines", ""), r.get("lines_word_timed", ""), r.get("lines_not_heard", ""),
                        r.get("confidence", ""), r.get("fits_recording", ""), " ".join(r.get("files", [])), r.get("reason", ""),
                        s.artist, s.title, round(s.seconds, 1) if s.seconds else ""])
    text = summary_lines(run)
    with open(os.path.join(run.run_dir, "summary.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(text) + "\n")
    took = (datetime.now() - run.started).total_seconds()
    n = lambda k: len(by.get(k, []))
    already = sum(1 for s in by.get("skipped", []) if "already" in s.result.get("reason", ""))
    h = ["<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
         "<title>WordLyrics report</title><style>%s</style></head><body><main>" % CSS,
         "<h1>WordLyrics report</h1><p class='dim'>%s &middot; %s &middot; took %s%s</p>" % (
             e(run.source), run.started.strftime("%Y-%m-%d %H:%M"), duration(took),
             " &middot; ran on " + e(run.engine.describe()) if run.engine else ""),
         "<div class='cards'>"]
    for num, label in ((len(run.songs), "songs"), (n("timed"), "word-timed in this run"), (already, "were word-timed already"),
                       (n("lines_only"), "line-timed only"), (n("not_timed"), "could not be timed"), (n("no_lyrics"), "no lyrics"),
                       (n("rejected"), "online lyrics rejected"), (n("failed"), "unreadable")):
        if num or label in ("songs", "word-timed in this run"):
            h.append("<div class='card'><b>%d</b>%s</div>" % (num, label))
    h.append("</div>")
    if run.interrupted:
        h.append("<div class='box attn'><h2>The run was stopped early</h2><p>Everything finished so far is complete and listed here. "
                 "Run it again to continue: songs that are done are skipped.</p></div>")
    k = run.check or {}
    clean = k.get("clean")
    h.append("<div class='box %s'><h2>Safety</h2>" % ("ok" if clean else "attn"))
    if run.backup_rec:
        b = run.backup_rec
        h.append("<p>An exact copy of the folder as it was before the run is in <code>%s</code> (%d files, %s, %s).</p>" % (
            e(b["path"]), b["files_total"], size(b["bytes_total"]),
            "every copied file read back and compared" if b["verified_byte_for_byte"] else "sizes compared"))
    if k:
        if clean:
            h.append("<p>After the run: <b>original audio preserved</b>, %d lyric files explicitly retimed after backup, "
                     "and %d lyric files written in total.</p>" % (len(k.get("retimed",[])),len(run.written)))
        else:
            h.append("<p>The differences below were not authorized lyric replacements by this run:</p><ul>")
            for key, label in (("missing", "no longer there"), ("changed", "changed (size or date)"), ("new_not_by_this_run", "new, not written by this run"),
                               ("written_but_gone", "written by this run but gone again")):
                if k.get(key):
                    h.append("<li>%d %s: %s%s</li>" % (len(k[key]), label, ", ".join("<code>%s</code>" % e(x) for x in k[key][:15]),
                                                       " &hellip;" if len(k[key]) > 15 else ""))
            h.append("</ul>")
    h.append("<p>Audio was only read. Retimed lyrics can be restored in the numbered menu. To take out new additions, run <code>WordLyrics.bat undo \"%s\"</code>; "
             "the files are moved into the tool's own folder, not deleted. The list of written files is in <code>%s</code>.</p></div>" % (
                 e(run.source), e(os.path.join(run.run_dir, "files written.csv"))))
    odd = [s for s in run.songs if s.result.get("lyrics") == "yours" and s.result.get("fits_recording") == "no"]
    if odd:
        h.append("<details><summary>Worth a look: lyrics that may belong to another song <span>(%d)</span></summary>"
                 "<p class='dim'>These songs already had lyrics, and those lyrics do not seem to match what is sung. Nothing was changed; "
                 "you may want to check them.</p><table>" % len(odd))
        h += ["<tr><td>%s</td><td class='dim'>%s</td></tr>" % (e(s.rel), e(s.result.get("lyrics_from", ""))) for s in odd]
        h.append("</table></details>")
    for key, title, blurb in GROUPS:
        songs = by.get(key, [])
        if not songs:
            continue
        h.append("<details%s><summary>%s <span>(%d)</span></summary><p class='dim'>%s</p><table>" % (
            " open" if key in ("network_error", "no_lyrics", "not_timed", "rejected", "failed") and len(songs) <= 40 else "", e(title), len(songs), e(blurb)))
        for s in sorted(songs,key=lambda s:s.rel.lower()):
            link=""
            if key in ("no_lyrics","not_timed","rejected","failed"):
                url="https://genius.com/search?"+urllib.parse.urlencode({"q":s.artist+" "+s.title})
                link=" <a target='_blank' rel='noopener noreferrer' href='%s'>Find a Genius page</a>" % e(url,quote=True)
            h.append("<tr><td>%s%s</td><td class='dim'>%s</td></tr>" % (e(s.rel),link,e(detail(s))))
        h.append("</table></details>")
    if run.problems:
        h.append("<div class='box attn'><h2>Notes</h2>%s</div>" % "".join("<p>%s</p>" % e(p) for p in run.problems))
    h.append("<p class='dim'>WordLyrics %s. Word times are worked out by a model from the audio; they are not supplied by a lyrics source. "
             "Every song is in <code>songs.csv</code> next to this page.</p></main></body></html>" % __version__)
    path = os.path.join(run.run_dir, "report.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(h))
    return path
