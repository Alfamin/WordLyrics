"""Finding lyrics for songs that have none (or only plain ones): LRCLIB (https://lrclib.net), a free
public lyrics database. Network only; never touches a song.

A wrong lyric is worse than no lyric, so a result is used only when it is clearly the same recording:
title and artist match, same version (remix / live / ...), and the length differs by at most 2.5 s.
What is sent: artist, title, album and length of the song. Nothing else.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import __version__
from . import library as L

API = "https://lrclib.net/api"
UA = "WordLyrics/%s (local music library tool; cached and throttled)" % __version__
MIN_INTERVAL = 0.35


class Offline(Exception):
    """The lyrics service cannot be reached at all."""


class Client:
    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.interval = MIN_INTERVAL
        self.last = 0.0
        self.errors_in_a_row = 0
        self.lock = threading.Lock()

    def _cache(self, key):
        return os.path.join(self.cache_dir, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")

    def get(self, endpoint, params, stop=None):
        """-> ('ok', data) | ('notfound', None) | ('error', message). Only ok / notfound are remembered."""
        url = API + endpoint + "?" + urllib.parse.urlencode(sorted(params.items()))
        cp = self._cache(url)
        if os.path.exists(cp):
            try:
                c = json.load(open(cp, encoding="utf-8"))
                # "not there" is asked again after a month: the database keeps growing
                if c["status"] == "ok" or time.time() - c.get("ts", 0) < 30 * 86400:
                    return c["status"], c["data"]
            except Exception:
                pass
        err = ""
        for attempt in range(3):
            if stop is not None and stop.is_set():
                return "error", "stopped"
            with self.lock:
                wait = self.interval - (time.time() - self.last)
                if wait > 0:
                    time.sleep(wait)
                self.last = time.time()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read().decode("utf-8"))
                self._remember(cp, "ok", data)
                self.interval = max(MIN_INTERVAL, self.interval * 0.9)
                self.errors_in_a_row = 0
                return "ok", data
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    self._remember(cp, "notfound", None)
                    self.errors_in_a_row = 0
                    return "notfound", None
                err = "HTTP %d" % e.code
                if e.code == 429:
                    self.interval = min(self.interval * 2, 30.0)
                    try:
                        pause = float(e.headers.get("Retry-After") or 0)
                    except ValueError:
                        pause = 0.0
                    time.sleep(min(pause or 20 * (attempt + 1), 90))
                elif 500 <= e.code < 600:
                    time.sleep(3 * (attempt + 1))
                else:
                    break
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
                err = type(e).__name__
                time.sleep(2 * (attempt + 1))
        self.errors_in_a_row += 1
        return "error", err

    def _remember(self, path, status, data):
        try:
            tmp = path + ".part"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"status": status, "data": data, "ts": time.time()}, fh, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            pass


# ------------------------------------------------------------------ is it the same recording?
VERSION_WORDS = [
    "remix", "live", "acoustic", "instrumental", "sped up", "slowed", "reverb", "demo", "radio edit",
    "extended", "karaoke", "cover", "vip", "flip", "mashup", "leak", "unreleased", "snippet", "a cappella",
    "acapella", "nightcore", "bass boosted", "clean",
]
FEAT_PAREN = re.compile(r"[\(\[]\s*(?:feat\.?|ft\.?|featuring|with|prod\.?)\b[^\)\]]*[\)\]]", re.I)
FEAT_TAIL = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$", re.I)
JUNK_TAIL = re.compile(r"\s*(@\S+|\(\s*\d{2,3}\s*\)|\[\s*\d{2,3}\s*\]|\bofficial\s+(?:audio|video)\b)\s*$", re.I)
NOISE_PAREN = re.compile(
    r"\s*[\(\[]\s*(?:[^\)\]]*\b(?:explicit|album version|single version|original version|remaster(?:ed)?|"
    r"deluxe|bonus track|mono|stereo|clean|\d{4})\b[^\)\]]*)[\)\]]", re.I)


def _ascii(s):
    from unidecode import unidecode
    return unidecode(s or "")


def strip_feat(s):
    return FEAT_TAIL.sub("", FEAT_PAREN.sub("", s)).strip()


def version_tokens(s):
    low = _ascii(s).lower()
    return {w for w in VERSION_WORDS if re.search(r"\b" + re.escape(w) + r"\b", low)}


def norm(s, dollar="s"):
    s = _ascii(strip_feat(s or "")).lower().replace("&", " and ")
    s = re.sub(r"[\(\[].*?[\)\]]", " ", s)       # bracketed text is compared separately (versions)
    s = s.replace("$", dollar)                   # A$AP: read the $ as an s, or drop it
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def sim(a, b):
    if not a or not b:
        return 0.0
    return max(difflib.SequenceMatcher(None, norm(a), norm(b)).ratio(),
               difflib.SequenceMatcher(None, norm(a, ""), norm(b, "")).ratio())


def artist_tokens(s):
    parts = re.split(r"\s*(?:,|;|/|&|\bx\b|\band\b|\bfeat\.?\b|\bft\.?\b|\bwith\b)\s*", _ascii(s), flags=re.I)
    return [p.strip() for p in parts if p.strip()]


def artist_ok(artist, albumartist, src_artist):
    if not src_artist:
        return False
    fa = [artist, albumartist] + artist_tokens(artist) + artist_tokens(albumartist)
    sa = [src_artist] + artist_tokens(src_artist)
    if any(x and y and sim(x, y) >= 0.85 for x in fa for y in sa):
        return True
    nf, ns = norm(artist + " " + albumartist), norm(src_artist)
    return bool(ns) and ns in nf


def title_variants(title):
    """Search variants, most likely first (noise like '(Explicit Album Version)' removed)."""
    t = title.strip()
    t2 = re.sub(r"^\s*\d{1,3}\s*[\.\-_]\s*", "", JUNK_TAIL.sub("", t).strip()).strip()
    t3 = NOISE_PAREN.sub("", t2).strip()
    out = []
    for v in (strip_feat(t3), t3, t2, t):
        if v and v not in out:
            out.append(v)
    return out


@dataclass
class Found:
    tier: int                      # L.LINE or L.PLAIN
    text: str                      # the lyrics as the service has them
    record: int = 0                # the service's record number, for the report
    title_sim: float = 0.0
    seconds_off: float = 0.0
    reasons: list = field(default_factory=list)


def judge(song, src_artist, src_title, src_seconds):
    """-> (same recording?, reasons why not)"""
    reasons = []
    best = max(sim(t, src_title) for t in title_variants(song.title))
    if best < 0.92:
        reasons.append("title differs")
    if not artist_ok(song.artist, song.albumartist, src_artist):
        reasons.append("artist differs")
    if version_tokens(song.title) != version_tokens(src_title):
        reasons.append("another version (remix / live / ...)")
    off = None if not src_seconds or not song.seconds else abs(float(song.seconds) - float(src_seconds))
    if off is None:
        reasons.append("length unknown")
    elif off > 2.5:
        reasons.append("length differs by %.0f s" % off)
    return not reasons, reasons, best, off


_TAGS = re.compile(r"\[\d+:\d+(?:[.:]\d+)?\]|<\d+:\d+(?:[.:]\d+)?>|\[[a-z]+:[^\]]*\]", re.I)


def words_of(text):
    return re.findall(r"[^\W_]+(?:'[^\W_]+)*", _TAGS.sub(" ", text or "").lower().replace("’", "'"))


def text_sim(a, b):
    a, b = words_of(a), words_of(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _usable(c, song):
    """One record of the service -> Found | 'instrumental' | None"""
    if c.get("instrumental"):
        return "instrumental"
    synced = (c.get("syncedLyrics") or "").replace("\r\n", "\n").strip()
    if synced and L.classify(synced) == L.LINE:
        stamps = [m for m in (L.LINE_TAG.match(ln) for ln in synced.split("\n")) if m]
        last = max(int(m.group(1)) * 60 + int(m.group(2)) for m in stamps)
        if last <= song.seconds + 3 and len(stamps) >= 5:      # must fit inside the song
            return Found(L.LINE, synced, c.get("id") or 0)
    plain = (c.get("plainLyrics") or "").replace("\r\n", "\n").strip()
    if len(plain) >= 40:
        return Found(L.PLAIN, plain, c.get("id") or 0)
    return None


def lookup(client, song, stop=None):
    """-> (Found | None, note). note says why nothing usable was found."""
    if not song.artist.strip() or not song.title.strip():
        return None, "no artist and title to search with"
    if not song.seconds:
        return None, "song length unknown"
    variants = []
    for t in title_variants(song.title):
        for a in dict.fromkeys([song.artist, *artist_tokens(song.artist)[:1]]):
            if (a, t) not in variants:
                variants.append((a, t))
    good, near, errors, instrumental, seen = [], [], 0, False, set()
    for a, t in variants[:3]:
        cands = []
        params = {"artist_name": a, "track_name": t, "duration": int(round(song.seconds))}
        if song.album:
            params["album_name"] = song.album
        st, data = client.get("/get", params, stop)
        if st == "ok" and isinstance(data, dict):
            cands.append(data)
        errors += st == "error"
        # the exact lookup already gave line-timed lyrics of the same recording: no need to search further
        if not (cands and judge(song, data.get("artistName", ""), data.get("trackName", ""), data.get("duration"))[0]
                and isinstance(_usable(data, song), Found) and _usable(data, song).tier == L.LINE):
            st, data = client.get("/search", {"artist_name": a, "track_name": t}, stop)
            if st == "ok" and isinstance(data, list):
                cands += data
            errors += st == "error"
        for c in cands:
            if not isinstance(c, dict) or c.get("id") in seen:
                continue
            seen.add(c.get("id"))
            ok, reasons, ts, off = judge(song, c.get("artistName", ""), c.get("trackName", ""), c.get("duration"))
            f = _usable(c, song)
            if f == "instrumental":
                instrumental = instrumental or ok
            elif f is not None:
                f.title_sim, f.seconds_off, f.reasons = ts, off if off is not None else 99.0, reasons
                (good if ok else near).append(f)
        if any(f.tier == L.LINE for f in good):
            break
    if not good:
        if errors and not near:
            return None, "the lyrics service could not be reached"
        if instrumental:
            return None, "listed as an instrumental"
        if near:
            return None, "found, but not surely the same recording (%s)" % near[0].reasons[0]
        return None, "not in the lyrics database"
    best = max(good, key=lambda f: (f.tier, -f.seconds_off))
    others = [f for f in good if f is not best]
    if others:      # other records of the same recording must not contradict the chosen one
        agree = sum(1 for f in others if text_sim(best.text, f.text) >= 0.60)
        if agree / len(others) < 0.5:
            return None, "the database has conflicting lyrics for this song"
    return best, ""
