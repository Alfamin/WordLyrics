"""Conservative finishing times from the existing voice envelope and CTC outputs.

This is a final display adjustment: word starts, line stamps, text and confidence
are untouched. It preserves joined word boundaries and only shortens an ending
by 80–250 ms when a sustained local fade has little remaining phonetic support.
Parameters were chosen on 40 Jamendo songs and validated on 39 reserved songs.
No extra inference or audio decoding is needed.
"""
from __future__ import annotations

import numpy as np

LEVEL_SHARE = .30
QUIET_S = .08
PHONETIC_SUPPORT = .10
SUPPORT_OCCUPANCY = .20
BLEND = .50
MAX_TRIM_S = .25
MIN_CHANGE_S = .08


def refine(rows, feat, vr, global_threshold, spf, fmt, need, gap_join):
    """Mutate only eligible ending tags/spans. Return the number adjusted.

The main renderer has already finalized line stamps and visibility smoothing.
Keeping this step last preserves those decisions, also for plain lyrics whose
unheard-line stamps are placed between their neighbouring lines.
"""
    if vr is None or len(vr) == 0 or global_threshold <= 0:
        return 0
    quiet_frames = max(1, int(round(QUIET_S / spf)))
    activity = None
    changed_total = 0
    for row in rows:
        if "ends" not in row or "elrc" not in row:
            continue
        starts, old_ends = row["starts"], row["ends"]
        ends = list(old_ends)
        changed = []
        for wi, (start, old, raw) in enumerate(zip(starts, old_ends, row["w"])):
            if raw is None or old-start < .12:
                continue
            if wi+1 < len(starts) and starts[wi+1]-old < gap_join:
                continue
            ctc_end = raw[1]*spf
            a = max(0, int(start/spf))
            b = min(len(vr), max(a+1, int(ctc_end/spf)))
            level = float(np.percentile(vr[a:b], 90)) if b > a else global_threshold
            threshold = max(global_threshold*.30, LEVEL_SHARE*level)
            k = max(a, min(len(vr)-1, int(ctc_end/spf)))
            stop = min(len(vr), int(old/spf)+quiet_frames)
            quiet = 0
            new = old
            for j in range(k, stop):
                quiet = quiet+1 if vr[j] < threshold else 0
                if quiet >= quiet_frames:
                    new = min(old, (j-quiet_frames+1)*spf)
                    break
            if old-new < .05:
                continue
            if activity is None:
                hearings = [np.asarray(feat[key], dtype=np.float32) for key in ("em", "emq") if key in feat]
                if not hearings:
                    return changed_total
                n = min(x.shape[0] for x in hearings)
                activity = np.exp(hearings[0][:n, 4:].max(axis=1))
                for hearing in hearings[1:]:
                    activity = np.maximum(activity, np.exp(hearing[:n, 4:].max(axis=1)))
            left = max(0, int(new/spf))
            right = min(len(activity), int(old/spf))
            tail = activity[left:right]
            if len(tail) and np.mean(tail >= PHONETIC_SUPPORT) >= SUPPORT_OCCUPANCY:
                continue
            new = max(old-MAX_TRIM_S, old+BLEND*(new-old))
            new = min(old, max(start+need(row["letters"][wi]), new))
            if old-new < MIN_CHANGE_S:
                continue
            ends[wi] = float(new)
            changed.append(wi)
        if not changed:
            continue
        # Reuse the already finalized stamp and whitespace verbatim.
        first_tag = row["elrc"].find("<")
        prefix = row["elrc"][:first_tag]
        parts = [f"<{fmt(s)}>{word}<{fmt(e)}>" for word, s, e in zip(row["text"].split(), starts, ends)]
        row["elrc"] = prefix+" ".join(parts)
        row["ends"] = ends
        for wi in changed:
            row["voice_ends"][wi] = max(starts[wi], min(row["voice_ends"][wi], ends[wi]))
        row["spans"] = [[round(s, 3), round(v, 3), round(e, 3)] for s, v, e in zip(starts, row["voice_ends"], ends)]
        changed_total += len(changed)
    return changed_total
