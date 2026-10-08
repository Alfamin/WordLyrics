"""Second alignment pass from trusted coarse anchors, reusing the same model emissions.

Soft line priors discourage isolated outliers. No supplied database timestamps are used.
Confident anchors, coverage and the existing quality gate remain protected.
"""
from copy import deepcopy

import numpy as np

PAD_S = 1.0
PRIOR_STRENGTH = .10
ANCHOR_SUPPORT = .15
TRUSTED_SUPPORT = .20
TRUSTED_DRIFT_S = .20
WEAK_DRIFT_S = 1.5
MIN_CHANGE_S = .08
MIN_SCORE_GAIN = 1.05


def refine(coarse, em, spf, duration, *, pad=PAD_S, strength=PRIOR_STRENGTH):
    from . import timing as T
    info = {"plain_passes": 2, "plain_refinement": "kept coarse", "plain_refinement_lines": 0}
    fine = deepcopy(coarse)
    paces = []
    for r in coarse:
        got = [(w, n) for w, n in zip(r.get("w") or [], r.get("letters") or []) if w is not None]
        for (a, n), (b, _) in zip(got, got[1:]):
            span = (b[0]-a[0]) * spf
            if a[2] >= TRUSTED_SUPPORT and b[2] >= TRUSTED_SUPPORT and .04 <= span <= 1.5 and n:
                paces.append(span/n)
    pace = float(np.clip(np.median(paces), .02, .15)) if paces else .06
    windows = {}
    for r in fine:
        got = r.get("w") or []
        letters = r.get("letters") or []
        anchors = [(i,w) for i,w in enumerate(got) if w is not None and w[2] >= ANCHOR_SUPPORT]
        if r.get("unheard") or len(anchors) < 3 or len(anchors) < .4 * len(got):
            continue
        sizes = np.array(letters, dtype=float)
        offsets = np.cumsum(sizes) - .5*sizes
        starts = [(.5*(w[0]+w[1])*spf - offsets[i]*pace) for i,w in anchors]
        beginning = float(np.median(starts))
        span = float(sizes.sum()*pace)
        margin = max(pad, .2*span)
        lo, hi = beginning-margin, beginning+span+margin
        for _, w in anchors:
            if w[2] >= TRUSTED_SUPPORT:
                lo = min(lo, w[0]*spf-.15)
                hi = max(hi, w[1]*spf+.15)
        windows[id(r)] = (max(0.0,lo), min(duration,hi), strength)
    info["plain_refinement_lines"] = len(windows)
    outliers = sum(1 for r in fine if id(r) in windows for w in r.get("w") or []
                   if w is not None and (w[0]*spf < windows[id(r)][0]-MIN_CHANGE_S
                                         or w[1]*spf > windows[id(r)][1]+MIN_CHANGE_S))
    if not outliers:
        info["plain_refinement"] = "kept coarse: no outlier to refine"
        return coarse, info
    if not T._one_pass(em, spf, fine, duration, None, soft_windows=windows):
        info["plain_refinement"] = "kept coarse: no candidate fits"
        return coarse, info
    old_prob, new_prob, changes, changed_old, changed_new = [], [], [], [], []
    for old, new in zip(coarse, fine):
        if old["text"] != new["text"] or len(old.get("w") or []) != len(new.get("w") or []):
            info["plain_refinement"] = "kept coarse: coverage changed"
            return coarse, info
        for a,b in zip(old.get("w") or [], new.get("w") or []):
            if (a is None) != (b is None):
                return coarse, info
            if a is None:
                continue
            drift = max(abs(b[0]-a[0]),abs(b[1]-a[1]))*spf
            if (a[2] >= TRUSTED_SUPPORT and drift > TRUSTED_DRIFT_S) or drift > WEAK_DRIFT_S:
                info["plain_refinement"] = "kept coarse: anchor moved too far"
                return coarse, info
            changes.append(drift)
            if drift >= MIN_CHANGE_S:
                changed_old.append(a[2])
                changed_new.append(b[2])
            old_prob.append(a[2])
            new_prob.append(b[2])
        old_scores = [x[2] for x in old.get("w") or [] if x is not None]
        new_scores = [x[2] for x in new.get("w") or [] if x is not None]
        if old_scores and np.median(old_scores) >= T.PLAIN_UNSURE and np.median(new_scores) < T.PLAIN_UNSURE:
            info["plain_refinement"] = "kept coarse: confidence fell"
            return coarse, info
    if T.find_unheard(fine, spf, stamps=False):
        info["plain_refinement"] = "kept coarse: new unheard line"
        return coarse, info
    if not changes or max(changes) < MIN_CHANGE_S:
        info["plain_refinement"] = "kept coarse: no perceptible change"
        return coarse, info
    if np.mean(new_prob) < .995 * np.mean(old_prob) or np.mean(changed_new) < MIN_SCORE_GAIN * np.mean(changed_old):
        info["plain_refinement"] = "kept coarse: no supporting gain"
        return coarse, info
    info["plain_refinement"] = "used refined alignment"
    return fine, info
