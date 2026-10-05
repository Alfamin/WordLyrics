"""What the models heard + a song's lyrics -> word-timed Enhanced LRC. CPU only, no files touched.

How a song is timed
  * The WHOLE song is aligned in one pass: every word of every line, in order. A line can therefore
    never take sounds that belong to the line before it, and lines cannot overlap.
  * Line timestamps (when the lyrics have them) are only a rough guide: a line's words have to lie
    between (its stamp - BAND) and (the next line's stamp + BAND). Inside that range the model decides.
    Lyrics without timestamps are placed by the model alone and have to pass a much stricter check.
  * A word is shown from where it starts to where the isolated voice stops; a pause shorter than
    GAP_JOIN is not shown as a pause; a word that would flash by borrows a little time from a neighbour.
  * A line the model cannot hear is never guessed: it stays an ordinary line at its own time.
  * The lyric text is copied through unchanged and every result is re-read and checked before use.
"""
from __future__ import annotations

import re

import numpy as np

from . import endings as E

SR = 16000
VOCAB = {c: i for i, c in enumerate(["<blank>", "<pad>", "</s>", "<unk>"] + list("aienoutsrmkldghybpwcvjzf'qx"))}
STAR = len(VOCAB)  # extra column (log-prob 0): absorbs audio that is not in the text
LINE_RE = re.compile(r"^(\s*)\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\](.*)$")
WORD_TAG = re.compile(r"<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>")
NEG = np.float32(-1e30)

# A line's words have to lie between (its stamp - BACK) and (the next line's stamp + FWD).
# Measured on 37 songs with ordinary human line stamps against hand-checked word timings:
# words within 0.3 s: 93.4% this way, 85.0% when every line is aligned inside its own span,
# 88% when the stamps are ignored. The exact widths hardly matter.
BAND = (0.35, 0.8)
LOOSER_FWD = (2.5, 6.0, 15.0)   # tried in turn when the words do not fit inside the normal guide
GAP_JOIN = 0.20     # a silence shorter than this between two words of a line is not shown as a pause
SNAP_MIN, SNAP_MAX = 0.02, 0.08   # a start may snap this far back to the moment the voice comes in
BORROW_MAX = 0.05   # a word boundary may be moved by at most this much to keep a short word visible
MIN_LINE_SHARE = 0.5   # lines the model cannot hear stay ordinary lines at their own stamp, so a song is still
                       # worth writing when a good part of it is word-timed; below half it is left as it was


# ------------------------------------------------------------------ text
ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "x x twenty thirty forty fifty sixty seventy eighty ninety".split()


def spell(n):
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + (ONES[n % 10] if n % 10 else "")
    if n < 1000:
        return ONES[n // 100] + "hundred" + (spell(n % 100) if n % 100 else "")
    if n < 1000000:
        return spell(n // 1000) + "thousand" + (spell(n % 1000) if n % 1000 else "")
    return ""


def word_tokens(word):
    """Characters the model can hear for one word (used for timing only, never written out)."""
    from unidecode import unidecode
    t = unidecode(word).lower()
    t = re.sub(r"\d+", lambda m: spell(int(m.group())) if len(m.group()) <= 6 else "", t)
    t = re.sub(r"[^a-z']", "", t).strip("'")
    return [VOCAB[c] for c in t]


def parse_lrc(text):
    """-> list of dicts: raw line, ts (float|None), text. Lines are kept verbatim."""
    rows = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        m = LINE_RE.match(raw)
        if not m or LINE_RE.match(m.group(5)):  # no stamp, or several stamps -> passthrough
            rows.append({"raw": raw, "ts": None, "text": ""})
            continue
        frac = m.group(4) or "0"
        ts = int(m.group(2)) * 60 + int(m.group(3)) + int(frac) / 10 ** len(frac)
        rows.append({"raw": raw, "ts": ts, "text": m.group(5), "stamp": raw[: m.start(5)]})
    return rows


def fmt(t):
    cs = int(round(max(t, 0) * 100))
    return f"{cs // 6000:02d}:{(cs % 6000) / 100:05.2f}"


def need(letters):
    """Shortest time a word should stay on screen for its sweep to be seen at all."""
    return min(max(0.05 + 0.01 * letters, 0.07), 0.11)


# ------------------------------------------------------------------ what the models heard
def combine(feat):
    """feat: dict with em (full mix), emq (isolated voice, quiet passages lifted), env, dur, spf.
    A letter counts as heard if either hearing has it. -> (em with STAR column, env, dur, spf)"""
    if "_combined" in feat:
        return feat["_combined"]
    ems = [np.asarray(feat[k]).astype(np.float32) for k in ("em", "emq") if k in feat]
    n = min(e.shape[0] for e in ems)
    em = ems[0][:n]
    for e in ems[1:]:
        em = np.logaddexp(em, e[:n])
    if len(ems) > 1:
        em = em - np.float32(np.log(len(ems)))
    em = np.concatenate([em, np.zeros((em.shape[0], 1), np.float32)], axis=1)   # the STAR column
    feat["_combined"] = (em, np.asarray(feat["env"]).astype(np.float64), float(feat["dur"]), float(feat["spf"]))
    return feat["_combined"]


def activity_from_env(env, T, spf):
    """Loudness of the isolated voice per alignment frame (env is its loudness every 5 ms)."""
    idx = np.minimum((np.arange(T + 1) * spf / 0.005).astype(np.int64), len(env))
    cs = np.concatenate([[0.0], np.cumsum(env ** 2)])
    vr = np.sqrt(np.maximum(cs[idx[1:]] - cs[idx[:-1]], 0) / np.maximum(idx[1:] - idx[:-1], 1))
    vr = np.convolve(vr, np.ones(3) / 3, mode="same")
    return vr, 0.10 * float(np.percentile(vr, 95))


def onsets_from_env(env, quiet_s=0.06):
    """Moments where the isolated voice comes in after at least quiet_s of silence."""
    e = np.convolve(env, np.ones(3) / 3, mode="same")
    loud = e >= 0.10 * float(np.percentile(e, 95))
    q = int(quiet_s / 0.005)
    edges = np.flatnonzero(loud[1:] & ~loud[:-1]) + 1
    quiet_before = np.concatenate([[0], np.cumsum(~loud)])
    return np.array([i * 0.005 for i in edges if i >= q and quiet_before[i] - quiet_before[i - q] == q])


# ------------------------------------------------------------------ alignment
def viterbi_banded(em, targets, lo, hi):
    """Best monotonic CTC path of `targets` through `em` where target j may only use frames lo[j]..hi[j].
    -> (first, last) frame per target, or None when no path fits."""
    T, L = em.shape[0], len(targets)
    S = 2 * L + 1
    if L == 0 or T < L:
        return None
    z = np.zeros(S, np.int64)
    z[1::2] = targets
    can_skip = np.zeros(S, bool)
    can_skip[3::2] = z[3::2] != z[1:-2:2]
    slo = np.zeros(S, np.int64)
    shi = np.full(S, T - 1, np.int64)
    slo[1::2] = lo
    slo[2::2] = lo          # the silence after a sound may start when the sound may
    shi[1::2] = hi
    shi[0:-1:2] = hi        # the silence before a sound has to be over when the sound has to be
    slo = np.maximum.accumulate(np.clip(slo, 0, T - 1))
    shi = np.minimum.accumulate(np.clip(shi, 0, T - 1)[::-1])[::-1]
    fa = np.searchsorted(shi, np.arange(T), side="left")     # first state still allowed at frame t
    fb = np.searchsorted(slo, np.arange(T), side="right")    # first state not yet allowed at frame t
    if fa[0] > 0 or fb[0] < 1:
        return None
    dpp = np.full(S + 2, NEG, np.float32)                    # dpp[s + 2] = best score ending in state s
    dpp[2] = em[0, 0]
    if fb[0] > 1:
        dpp[3] = em[0, z[1]]
    bps = [None] * T
    for t in range(1, T):
        a, b = int(fa[t]), int(fb[t])
        if b <= a:
            return None
        prev = dpp[a: b + 2]
        best = prev[2:].copy()
        k = np.zeros(b - a, np.int8)
        adv = prev[1:-1]
        m = adv > best
        best[m] = adv[m]
        k[m] = 1
        skp = np.where(can_skip[a:b], prev[:-2], NEG)
        m = skp > best
        best[m] = skp[m]
        k[m] = 2
        new = best + em[t, z[a:b]]
        pa = int(fa[t - 1])
        if a > pa:
            dpp[pa + 2: a + 2] = NEG
        dpp[a + 2: b + 2] = new
        bps[t] = k
    s = S - 1 if dpp[S + 1] >= dpp[S] else S - 2
    if dpp[s + 2] <= NEG / 2:
        return None
    first = np.full(L, -1, np.int64)
    last = np.full(L, -1, np.int64)
    for t in range(T - 1, -1, -1):
        if s & 1:
            j = s >> 1
            if last[j] < 0:
                last[j] = t
            first[j] = t
        if t:
            s -= int(bps[t][s - int(fa[t])])
    return first, last


def _one_pass(em, spf, rows, duration, band):
    """One alignment for the whole song. Adds r['w'] = [(start_frame, end_frame, prob) | None per word]
    to every line that has something to hear. band=None ignores the line stamps."""
    T = em.shape[0]
    timed = [r for r in rows if r["ts"] is not None]
    for r in timed:
        r.pop("w", None)
    targets, lo, hi, marks = [STAR], [0], [T - 1], []
    for i, r in enumerate(timed):
        words = r["text"].split()
        toks = [word_tokens(w) for w in words]
        if not any(toks) or r.get("unheard"):
            continue
        r["letters"] = [len(tk) for tk in toks]
        nxt = next((x["ts"] for x in timed[i + 1:] if x["ts"] > r["ts"]), None)
        if nxt is None:
            nxt = min(duration, r["ts"] + max(8.0, 0.5 * len(words)))
        back, fwd = band if isinstance(band, tuple) else (band, band)
        f_lo = 0 if band is None else max(0, int((r["ts"] - back) / spf))
        f_hi = T - 1 if band is None else min(T - 1, int((nxt + fwd) / spf))
        r["nxt"] = nxt
        spans = []
        for tk in toks:
            spans.append((len(targets), len(targets) + len(tk)))
            targets += tk
            lo += [f_lo] * len(tk)
            hi += [f_hi] * len(tk)
        marks.append((r, spans))
        targets.append(STAR)       # whatever is sung between two lines but is not in the text
        lo.append(f_lo)
        hi.append(T - 1)
    if not marks:
        return False
    res = viterbi_banded(em, np.array(targets), np.array(lo), np.array(hi))
    if res is None:
        return False
    first, last = res
    for r, spans in marks:
        out = []
        for a, b in spans:
            if b == a:
                out.append(None)
                continue
            p = float(np.mean([np.exp(em[first[j]: last[j] + 1, targets[j]]).mean() for j in range(a, b)]))
            out.append((int(first[a]), int(last[b - 1]) + 1, p))
        r["w"] = out
    return True


UNHEARD_PROB = 0.05   # below this the model did not really hear the word
STALL_MIN = 2.0       # an unheard word that "lasts" this long before the next word of its line ...
STALL_PACE = 8        # ... and this many times longer than words of its length normally take: the line was not found
LATE_MAX = 3.0        # an unheard line that starts this long after its stamp was not found either


def find_unheard(rows, spf, stamps=True):
    """Lines where the highlight would freeze on a word the model never heard and then rush through the
    rest (faded second voices, samples, ad-libs sung far in the background)."""
    pairs = []
    for r in rows:
        w = r.get("w")
        if not w:
            continue
        got = [(x, n) for x, n in zip(w, r["letters"]) if x is not None]
        pairs += [(r, a, n, (b[0] - a[0]) * spf) for (a, n), (b, _) in zip(got, got[1:])]
    heard = [d / max(n, 1) for _, a, n, d in pairs if a[2] >= 0.10]
    pace = float(np.median(heard)) if heard else 0.08          # seconds per letter in this song
    bad = []
    for r, a, n, d in pairs:
        if a[2] < UNHEARD_PROB and d > max(STALL_MIN, STALL_PACE * pace * max(n, 3)) and r not in bad:
            bad.append(r)
    for r in rows if stamps else []:    # an unheard line that drifted far away from its stamp
        got = [x for x in r.get("w") or [] if x is not None]
        if got and r not in bad and np.median([x[2] for x in got]) < UNHEARD_PROB and got[0][0] * spf - r["ts"] > LATE_MAX:
            bad.append(r)
    return bad


def align_global(em, spf, rows, duration, band=BAND, drop_unheard=True):
    """Align the whole song; lines the model cannot hear are taken out (they stay line-level) and the
    song is aligned again, so they cannot take sounds that belong to the lines around them."""
    for r in rows:
        r.pop("unheard", None)
    if not _one_pass(em, spf, rows, duration, band):
        return False
    for _ in range(4):
        bad = find_unheard(rows, spf, band is not None) if drop_unheard else []
        if not bad:
            break
        for r in bad:
            r["unheard"] = True
        if not _one_pass(em, spf, rows, duration, band):
            return False
    return True


# ------------------------------------------------------------------ from frames to what is shown
def _snap(t, lo, on):
    if on is None or len(on) == 0:
        return t
    i = int(np.searchsorted(on, t - SNAP_MIN, side="right")) - 1
    if i >= 0 and on[i] >= t - SNAP_MAX and on[i] >= lo:
        return float(on[i])
    return t


def keep_visible(starts, ends, letters):
    """A word that would be on screen too briefly to see borrows time from the word before or after it.
    Only boundaries between two words that touch are moved, and never by more than BORROW_MAX."""
    n = len(starts)
    s, e = list(starts), list(ends)
    moved = [0.0] * (n + 1)                      # how far the boundary in front of word i has been moved
    for _ in range(3):
        changed = False
        for i in range(n):
            lack = need(letters[i]) - (e[i] - s[i])
            if lack <= 0.004:
                continue
            opts = []
            if i > 0 and abs(e[i - 1] - s[i]) < 1e-6:                  # touches the word before
                spare = (e[i - 1] - s[i - 1]) - need(letters[i - 1])
                opts.append((min(spare, BORROW_MAX + moved[i]), "prev"))
            if i + 1 < n and abs(e[i] - s[i + 1]) < 1e-6:              # touches the word after
                spare = (e[i + 1] - s[i + 1]) - need(letters[i + 1])
                opts.append((min(spare, BORROW_MAX - moved[i + 1]), "next"))
            for room, side in sorted(opts, reverse=True):
                take = min(lack, room)
                if take <= 0.004:
                    continue
                if side == "prev":
                    s[i] -= take
                    e[i - 1] = s[i]
                    moved[i] -= take
                else:
                    e[i] += take
                    s[i + 1] = e[i]
                    moved[i + 1] += take
                lack -= take
                changed = True
        if not changed:
            break
    return s, e


def render(rows, spf, duration, vr=None, thr=None, on=None, smooth=True, band=BAND):
    """Turn the aligned frames into '<start>word<end>' lines. Mutates rows (adds 'elrc', 'spans', ...)."""
    timed = [r for r in rows if r["ts"] is not None]
    stats = {"lines": 0, "lines_aligned": 0, "words": 0, "words_aligned": 0, "conf": [], "lines_collapsed": 0,
             "lines_unheard": sum(1 for r in rows if r.get("unheard")),
             "stamp_earlier": 0, "stamp_later": 0, "borrowed": 0}
    lined = [r for r in timed if r["text"].split()]
    stats["lines"] = len(lined)
    stats["words"] = sum(len(r["text"].split()) for r in lined)
    done, floor = [], 0.0
    for r in lined:
        if "w" not in r:
            continue
        res = r["w"]
        starts, prev = [], None
        for x in res:
            if x is None:
                starts.append(prev)
                continue
            t = x[0] * spf
            t = _snap(t, (prev + 0.02) if prev is not None else max(floor, t - SNAP_MAX), on)
            t = t if prev is None else max(t, prev)
            starts.append(t)
            prev = t
        first = next(s for s in starts if s is not None)
        starts = [first if s is None else s for s in starts]
        shown = [fmt(s) for s, x in zip(starts, res) if x is not None]
        if len(shown) >= 3 and max(shown.count(x) for x in set(shown)) >= 0.5 * len(shown):
            stats["lines_collapsed"] += 1      # the words pile up on one moment: not really found
            continue
        r["starts"] = starts
        floor = max(x[1] for x in res if x is not None) * spf
        done.append(r)
    for i, r in enumerate(done):
        words, res, starts = r["text"].split(), r["w"], r["starts"]
        got = [x for x in res if x is not None]
        nxt_start = done[i + 1]["starts"][0] if i + 1 < len(done) else duration
        ends, voice_ends = [], []
        for wi in range(len(words)):
            s = starts[wi]
            limit = starts[wi + 1] if wi + 1 < len(words) else max(nxt_start, s)
            if res[wi] is None:
                e = s
            else:
                ef = res[wi][1]
                if vr is not None:             # the voice is still sounding: the note is being held
                    lim_f, quiet, k = min(len(vr), int(limit / spf)), 0, ef
                    while k < lim_f and quiet < 3:
                        if vr[k] < thr:
                            quiet += 1
                        else:
                            ef = k + 1
                            if vr[k] >= 1.5 * thr:   # only a clearly sounding voice restarts the count, so a
                                quiet = 0            # breath or echo hovering at the threshold is not a held note
                        k += 1
                e = ef * spf
            e = min(max(e, s + 0.02), max(limit, s))
            voice_ends.append(e)
            if wi + 1 < len(words) and limit - e < GAP_JOIN:
                e = limit                      # no real pause: let the highlight flow into the next word
            ends.append(e)
        if smooth:
            letters = [len(word_tokens(w)) for w in words]
            s2, e2 = keep_visible(starts, ends, letters)
            stats["borrowed"] += sum(1 for p, q in zip(starts, s2) if abs(p - q) > 0.004)
            starts, ends = s2, e2
        r["starts"], r["ends"], r["voice_ends"] = starts, ends, voice_ends
        stats["lines_aligned"] += 1
        stats["words_aligned"] += len(got)
        stats["conf"] += [x[2] for x in got]
    # line stamps: a line becomes the current one no later than its first word, and not while the
    # line before it is still being sung
    prev_end = 0.0
    for r in lined:
        if "starts" not in r:
            prev_end = max(prev_end, r["ts"])
            continue
        r["ts_out"] = min(r["starts"][0], max(r["ts"], prev_end))
        prev_end = r["ends"][-1]
    ceiling = float("inf")
    for r in reversed(timed):                   # never jump over a line that keeps its own stamp
        if "starts" in r:
            r["ts_out"] = min(r["ts_out"], ceiling)
            ceiling = min(ceiling, r["ts_out"])
        else:
            ceiling = min(ceiling, r["ts"])
    floor_ts = 0.0
    for r in timed:                             # ... and never start before a line above that keeps its own stamp
        if "starts" not in r:
            floor_ts = max(floor_ts, r["ts"])
        elif r["ts_out"] < floor_ts:
            r["ts_out"] = floor_ts
            r["starts"] = [max(s, floor_ts) for s in r["starts"]]
            r["ends"] = [max(e, s) for s, e in zip(r["starts"], r["ends"])]
            r["voice_ends"] = [max(v, s) for s, v in zip(r["starts"], r["voice_ends"])]
    for r in lined:
        if "starts" not in r:
            continue
        words = r["text"].split()
        parts = [f"<{fmt(s)}>{w}<{fmt(e)}>" for w, s, e in zip(words, r["starts"], r["ends"])]
        lead = r["text"][: len(r["text"]) - len(r["text"].lstrip())]
        stamp = r["stamp"]
        if r.get("plain"):                      # the lyrics had no stamps: the line starts with its first word
            stamp = f"[{fmt(r['ts_out'])}]"
        elif fmt(r["ts_out"]) != fmt(r["ts"]):
            stamp = stamp[: len(stamp) - len(stamp.lstrip())] + f"[{fmt(r['ts_out'])}]"
            stats["stamp_earlier" if r["ts_out"] < r["ts"] else "stamp_later"] += 1
        r["elrc"] = stamp + lead + " ".join(parts)
        r["spans"] = [[round(s, 3), round(v, 3), round(e, 3)] for s, v, e in zip(r["starts"], r["voice_ends"], r["ends"])]
    return stats


def verify(elrc_text, rows, duration, band):
    """Re-read what is about to be written: text identical, stamps in order and inside the allowed range."""
    problems = []
    out_lines = elrc_text.split("\n")
    if len(out_lines) != len(rows):
        return ["line count changed"]
    last_stamp = -1.0
    back, fwd = band if isinstance(band, tuple) else (band, band)
    for o, r in zip(out_lines, rows):
        plain_o = " ".join(WORD_TAG.sub("", o).split())
        plain_r = " ".join(r["raw"].split())
        mo = LINE_RE.match(plain_o) if r["ts"] is not None else None
        if mo:
            mr = LINE_RE.match(plain_r)
            if " ".join(mo.group(5).split()) != " ".join(mr.group(5).split()):
                problems.append("text changed")
            frac = mo.group(4) or "0"
            so = int(mo.group(2)) * 60 + int(mo.group(3)) + int(frac) / 10 ** len(frac)
            if band is not None and (so < r["ts"] - back - SNAP_MAX - 0.03 or so > r.get("nxt", r["ts"]) + fwd + 0.03):
                problems.append("line stamp outside its range")
            if so < last_stamp - 0.006:
                problems.append("line stamps out of order")
            last_stamp = max(last_stamp, so)
        elif plain_o != plain_r:
            problems.append("text changed")
        ts = [int(x) * 60 + float(y) for x, y in re.findall(r"<(\d{1,3}):(\d{2}\.\d{2})>", o)]
        if any(q < p for p, q in zip(ts, ts[1:])):
            problems.append("word times out of order")
        if ts and ts[-1] > duration + 1:
            problems.append("word time beyond the end of the song")
    return problems


def process(feat, text, band=BAND):
    """Line-timed lyrics. -> (elrc text | None, rows, stats, problems)"""
    em, env, dur, spf = combine(feat)
    rows = parse_lrc(text)
    # the stamps are only a guide: where they are too crowded for the words to fit (several lines stamped at
    # almost the same moment), the guide is loosened step by step instead of giving up on the song
    for b in [band] + [(band[0], f) for f in LOOSER_FWD if f > band[1]]:
        if align_global(em, spf, rows, dur, b):
            band = b
            break
    else:
        return None, rows, {"lines": 0, "lines_aligned": 0}, ["no alignment fits"]
    vr, thr = activity_from_env(env, em.shape[0], spf)
    stats = render(rows, spf, dur, vr, thr, onsets_from_env(env), True, band)
    _hearing(stats, rows)
    stats["end_refinements"] = E.refine(rows, feat, vr, thr, spf, fmt, need, GAP_JOIN)
    elrc = "\n".join(r.get("elrc", r["raw"]) for r in rows)
    return elrc, rows, stats, verify(elrc, rows, dur, band)


def _hearing(stats, rows):
    """How clearly the model heard these words in this recording (used to tell wrong lyrics from right ones)."""
    probs = [x[2] for r in rows for x in (r.get("w") or []) if x is not None]
    stats["word_prob_median"] = float(np.median(probs)) if probs else 0.0
    stats["words_heard_share"] = float(np.mean(np.array(probs) >= 0.10)) if probs else 0.0


# ------------------------------------------------------------------ lyrics without any timestamps
_SECTION = r"(?:intro|outro|verse|chorus|hook|bridge|pre-?chorus|post-?chorus|refrain|interlude|instrumental)"
HEADING_RE = re.compile(r"^\s*(\[[^\]]*\]|\(\s*" + _SECTION + r"\b[^()]{0,30}\)|" + _SECTION + r"\b[^():]{0,12}:)\s*$",
                        re.I)                                              # "[Chorus]", "(Verse 1)", "Hook:"


def plain_rows(text):
    """Plain lyrics -> one row per sung line (same shape as parse_lrc's). Headings and empty lines are not
    sung and are left out; the text of every other line is kept exactly."""
    rows = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        ln = raw.strip()
        if not ln or HEADING_RE.match(ln) or not any(word_tokens(w) for w in ln.split()):
            continue
        rows.append({"raw": ln, "ts": 0.0, "text": ln, "stamp": "", "plain": True})
    return rows


# Without line stamps a song is only used when the model was sure of nearly all of it. Measured on 217 songs
# whose human line stamps were hidden: songs passing this have 97.4% of their lines within 1 s of the human
# stamp, the worst passing song 85%; 4 songs in 10 pass.
PLAIN_UNSURE = 0.10          # a line the model is less sure of than this is "unsure"
PLAIN_MAX_UNSURE = 0.20      # at most this share of the lines may be unsure
PLAIN_MAX_UNHEARD = 0.05     # at most this share may be not heard at all
PLAIN_MIN_LINES = 6


def process_plain(feat, text):
    """Lyrics WITHOUT timestamps: the model places every line itself (one pass over the whole song).
    -> (elrc text | None, rows, stats, problems). Whether the result may be used is decided by plain_gate."""
    em, env, dur, spf = combine(feat)
    rows = plain_rows(text)
    if not rows or not align_global(em, spf, rows, dur, None):
        return None, rows, {"lines": len(rows), "lines_aligned": 0}, ["no alignment fits"]
    _h = {}
    _hearing(_h, rows)
    for r in rows:
        got = [x for x in r.get("w") or [] if x is not None]
        r["ts"] = got[0][0] * spf if got else None          # None: the line was not heard
        r["prob"] = float(np.median([x[2] for x in got])) if got else 0.0
        if got and r["prob"] < UNHEARD_PROB:
            del r["w"]      # barely heard: the line keeps the place the model gave it, but its words are not timed
    vr, thr = activity_from_env(env, em.shape[0], spf)
    on = onsets_from_env(env)
    stats = render(rows, spf, dur, vr, thr, on, True, None)
    stats.update(_h)
    # a line without word timing still needs a moment to be shown at: where the model put it, or (not
    # heard at all) spread over the time between the lines around it
    i, last, prev_end = 0, 0.0, 0.0
    while i < len(rows):
        r = rows[i]
        if "starts" in r:
            last, prev_end = r["ts_out"], r["ends"][-1]
            i += 1
            continue
        j = i
        while j < len(rows) and "starts" not in rows[j]:
            j += 1
        nxt = rows[j]["ts_out"] if j < len(rows) else dur
        k = int(np.searchsorted(on, prev_end))
        t0 = float(on[k]) if k < len(on) and on[k] < nxt else prev_end   # where the voice next comes in
        for m, q in enumerate(rows[i:j]):
            t = q["ts"] if q["ts"] is not None else t0 + (nxt - t0) * m / (j - i)
            last = min(max(t, last), nxt)
            q["ts_out"] = last
            q["elrc"] = f"[{fmt(last)}]" + q["text"]
        i = j
    stats.update(lines=len(rows), lines_unheard=sum(1 for r in rows if r.get("unheard")),
                 lines_unsure=sum(1 for r in rows if r["prob"] < PLAIN_UNSURE),
                 line_prob_median=float(np.median([r["prob"] for r in rows])), voice_s=float((vr >= thr).sum() * spf))
    stats["end_refinements"] = E.refine(rows, feat, vr, thr, spf, fmt, need, GAP_JOIN)
    elrc = "\n".join(r["elrc"] for r in rows)
    return elrc, rows, stats, verify_plain(elrc, rows, dur)


def plain_gate(stats):
    """-> None when the plain-mode result may be used, else the reason it is refused."""
    n = stats.get("lines") or 0
    if n < PLAIN_MIN_LINES:
        return "too few lines to judge"
    if stats["lines_unsure"] / n > PLAIN_MAX_UNSURE:
        return "the model is unsure of %d%% of the lines" % round(100 * stats["lines_unsure"] / n)
    if stats["lines_unheard"] / n > PLAIN_MAX_UNHEARD:
        return "%d%% of the lines were not heard" % round(100 * stats["lines_unheard"] / n)
    return None


def verify_plain(elrc_text, rows, duration):
    """Re-read what is about to be written: every sung line present with its text unchanged, in order,
    every line stamped, stamps and word times in order and inside the song."""
    problems = []
    out_lines = elrc_text.split("\n")
    if len(out_lines) != len(rows):
        return ["line count changed"]
    last = -1.0
    for o, r in zip(out_lines, rows):
        mo = LINE_RE.match(WORD_TAG.sub("", o))
        if not mo:
            problems.append("line without a stamp")
            continue
        if " ".join(mo.group(5).split()) != " ".join(r["raw"].split()):
            problems.append("text changed")
        frac = mo.group(4) or "0"
        so = int(mo.group(2)) * 60 + int(mo.group(3)) + int(frac) / 10 ** len(frac)
        if so < last - 0.006:
            problems.append("line stamps out of order")
        last = max(last, so)
        ts = [int(x) * 60 + float(y) for x, y in re.findall(r"<(\d{1,3}):(\d{2}\.\d{2})>", o)]
        if any(q < p for p, q in zip(ts, ts[1:])) or (ts and ts[0] < so - 0.011):
            problems.append("word times out of order")
        if (ts and ts[-1] > duration + 1) or so > duration + 1:
            problems.append("time beyond the end of the song")
    return problems


# ------------------------------------------------------------------ do these lyrics belong to this recording?
# How sure the model is of the words says little: lyrics of a DIFFERENT song score nearly as well as the
# right ones. What does tell them apart: right lyrics fit much better in their real order than with their
# lines dealt out in another order; wrong lyrics fit equally badly both ways. Measured on 150 songs, each
# with its own lyrics and with those of another song of the same length (ratio = real order / shuffled):
#   line-timed lyrics:    wrong ones 1.26 at most, right ones typically 2.2 -> split at 1.35
#                         (0 of 150 wrong accepted, 4 of 150 right refused)
#   lyrics without times: wrong ones 1.25 at most, right ones typically 1.7 -> split at 1.28
#                         (0 of 150 wrong accepted, 10 of 150 right refused)
FITS_LINE, FITS_PLAIN = 1.35, 1.28


def _fit_score(em, spf, dur, rows, band):
    for r in rows:
        r.pop("unheard", None)
    for b in ([band] + [(band[0], f) for f in LOOSER_FWD]) if band else [None]:
        if _one_pass(em, spf, rows, dur, b):
            break
    else:
        return None
    p = [x[2] for r in rows for x in (r.get("w") or []) if x is not None]
    return float(np.mean(p)) if p else None


def belongs(feat, text, plain):
    """-> (True | False | None, ratio). None: cannot be judged (too short, or the same few lines over and over)."""
    em, env, dur, spf = combine(feat)
    rows = plain_rows(text) if plain else parse_lrc(text)
    idx = [i for i, r in enumerate(rows) if r["ts"] is not None and r["text"].split()]
    n = len(idx)
    if n < 6:
        return None, None
    key = [" ".join(rows[i]["text"].lower().split()) for i in idx]
    half = max(1, n // 2)
    same, k = min((sum(key[j] == key[(j + k) % n] for j in range(n)), abs(k - half), k) for k in range(max(1, n // 4), max(2, 3 * n // 4)))[::2]
    if same > 0.5 * n:
        return None, None
    moved = [dict(r) for r in rows]
    for j, i in enumerate(idx):
        moved[i]["text"] = rows[idx[(j + k) % n]]["text"]
    band = None if plain else BAND
    real = _fit_score(em, spf, dur, [dict(r) for r in rows], band)
    shuf = _fit_score(em, spf, dur, moved, band)
    if real is None:
        return False, 0.0
    if shuf is None or shuf <= 0:
        return None, None
    ratio = real / shuf
    return ratio >= (FITS_PLAIN if plain else FITS_LINE), round(ratio, 3)


def line_level(elrc_text):
    """The same lyrics without the word tags: an ordinary .lrc for players that cannot read word timing."""
    return "\n".join(WORD_TAG.sub("", ln).rstrip() for ln in elrc_text.split("\n"))
