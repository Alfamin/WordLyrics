"""Looking at a music folder: which songs are there, what lyrics do they already have. READ ONLY."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

AUDIO_EXT = {".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".oga", ".opus", ".wav", ".aiff", ".aif", ".wma", ".aac", ".ape", ".wv"}
WORD_SIDECARS = (".lyricsfile", ".ttml")       # the player reads these before an .elrc
NONE, INSTRUMENTAL, PLAIN, LINE, WORD = 0, 1, 2, 3, 4
TIER_NAME = {NONE: "none", INSTRUMENTAL: "instrumental", PLAIN: "plain", LINE: "line-timed", WORD: "word-timed"}

LINE_TAG = re.compile(r"^\s*\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
WORD_TAG = re.compile(r"<(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?>")
META_TAG = re.compile(r"^\s*\[[a-zA-Z]{2,}:[^\]]*\]\s*$")  # [ar:..] [ti:..] etc.
INSTRUMENTAL_RE = re.compile(r"^\s*\[?\(?instrumental\)?\]?\s*$", re.I)


def classify(text):
    """What kind of lyrics is this text? WORD only if real per-word timestamps are present."""
    if not text or not text.strip():
        return NONE
    if INSTRUMENTAL_RE.match(text.strip()):
        return INSTRUMENTAL
    lines = [ln for ln in text.splitlines() if ln.strip() and not META_TAG.match(ln)]
    timed = word_lines = 0
    for ln in lines:
        lm = LINE_TAG.match(ln)
        if lm:
            timed += 1
            body = ln[lm.end():]
            if WORD_TAG.search(body) and re.search(r"<[^>]+>\s*\S", body):
                word_lines += 1
    if timed and timed >= 0.5 * len(lines):
        return WORD if word_lines / timed >= 0.5 else LINE
    return PLAIN


@dataclass(eq=False)
class Song:
    path: str
    rel: str                       # path inside the library, for display and reports
    size: int = 0
    seconds: float = 0.0
    artist: str = ""
    title: str = ""
    album: str = ""
    albumartist: str = ""
    tags_from: str = ""            # "tags" | "file name" | ""
    tier: int = NONE               # best lyrics already there
    lyrics_from: str = ""          # where they are: "file.lrc", "inside the song file", "file.txt"
    text: str = ""                 # those lyrics (never shown, never changed)
    has_lrc: bool = False
    skip: str = ""                 # set when the song is not to be handled at all, with the reason
    # filled in while running
    found: object = None           # fetch.Found
    fetch_note: str = ""
    result: dict = field(default_factory=dict)

    @property
    def base(self):
        return os.path.splitext(self.path)[0]

    @property
    def name(self):
        return os.path.splitext(os.path.basename(self.path))[0]


LONG_AT = 240                                   # Windows: from this length on a path needs its long form
LONG = "\\\\?\\"


def _long_form(path):
    path = os.path.abspath(path)
    if os.name != "nt" or path.startswith(LONG):
        return path
    return LONG + "UNC\\" + path[2:] if path.startswith("\\\\") else LONG + path


def _usual_form(path):
    """Paths of ordinary length are used the ordinary way; only very long ones keep the long form,
    which every Windows program can open whatever the computer's long-path setting is."""
    if not path.startswith(LONG) or len(path) - len(LONG) >= LONG_AT:
        return path
    return "\\\\" + path[len(LONG) + 4:] if path.startswith(LONG + "UNC\\") else path[len(LONG):]


def walk(root, exclude=(), unreadable=None):
    """Every file under root (links to other folders are not followed). -> [(path, rel, size, mtime_ns)], skipped links.
    Folders and files that cannot be read (no permission) are added to the list `unreadable`, never passed over silently."""
    files, links = [], []
    excl = [os.path.normcase(os.path.abspath(e)) for e in exclude if e]
    top = _long_form(root)

    def failed(err):
        if unreadable is not None:
            unreadable.append(os.path.relpath(getattr(err, "filename", None) or top, top))
    for dirpath, dirnames, filenames in os.walk(top, followlinks=False, onerror=failed):
        keep = []
        for d in sorted(dirnames):
            full = os.path.join(dirpath, d)
            if os.path.islink(full) or _is_junction(full):
                links.append(os.path.relpath(full, top))
            elif os.path.normcase(_usual_form(full)) not in excl:
                keep.append(d)
        dirnames[:] = keep
        for f in sorted(filenames):
            full = os.path.join(dirpath, f)
            try:
                if os.path.islink(full):
                    links.append(os.path.relpath(full, top))
                    continue
                st = os.stat(full)
            except OSError as e:
                failed(e)
                continue
            files.append((_usual_form(full), os.path.relpath(full, top), st.st_size, st.st_mtime_ns))
    return files, links


def _is_junction(path):
    try:
        return bool(getattr(os.path, "isjunction", lambda p: False)(path))
    except OSError:
        return False


def _read_text(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read(4 * 1024 * 1024)
    except OSError:
        return ""
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeError:
            continue
    return raw.decode("utf-8", "replace")


ADTS_RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350)


def adts_seconds(path):
    """A bare .aac file does not say how long it is, and the usual guess from its bit rate can be 5% off:
    too far for the length check of the lyrics search. Counting its frames gives the exact length.
    -> seconds, or 0 when the file is not a plain run of frames."""
    try:
        with open(path, "rb") as fh:
            d = fh.read(64 * 1024 * 1024)
    except OSError:
        return 0.0
    i = 0
    if d[:3] == b"ID3" and len(d) > 10:
        i = 10 + ((d[6] & 0x7F) << 21 | (d[7] & 0x7F) << 14 | (d[8] & 0x7F) << 7 | (d[9] & 0x7F))
    n, blocks, rate = len(d), 0, 0
    while i + 7 <= n and d[i] == 0xFF and (d[i + 1] & 0xF6) == 0xF0:
        idx = (d[i + 2] >> 2) & 0xF
        size = ((d[i + 3] & 3) << 11) | (d[i + 4] << 3) | (d[i + 5] >> 5)
        if size < 7 or idx >= len(ADTS_RATES):
            break
        rate = ADTS_RATES[idx]
        blocks += (d[i + 6] & 3) + 1
        i += size
    if blocks < 10 or not rate or i < 0.98 * n:
        return 0.0
    return blocks * 1024.0 / rate


def embedded(path):
    """Tags and lyrics stored inside the file. -> (info dict, [lyrics texts])"""
    import mutagen
    info, texts = {}, []
    m = mutagen.File(path)
    if m is None:
        raise ValueError("not a known audio format")
    info["seconds"] = float(getattr(m.info, "length", 0) or 0)
    if type(m).__name__ == "AAC":
        info["seconds"] = adts_seconds(path) or info["seconds"]
    t = m.tags
    kind = type(t).__name__ if t is not None else ""
    if t is None:
        pass
    elif "ID3" in kind or hasattr(t, "getall"):                      # mp3, wav, aiff
        def g(k):
            fr = t.get(k)
            return str(fr.text[0]) if fr is not None and getattr(fr, "text", None) else ""
        info.update(artist=g("TPE1"), title=g("TIT2"), album=g("TALB"), albumartist=g("TPE2"))
        for fr in t.getall("USLT"):
            texts.append(fr.text)
        for fr in t.getall("SYLT"):
            try:
                if fr.format == 2:
                    texts.append("\n".join(f"[{int(ms) // 60000:02d}:{(ms % 60000) / 1000:05.2f}]{txt.strip()}" for txt, ms in fr.text))
                else:
                    texts.append("\n".join(txt for txt, _ in fr.text))
            except Exception:
                continue
    elif "MP4" in kind:
        def g(k):
            v = t.get(k)
            return str(v[0]) if v else ""
        info.update(artist=g("\xa9ART"), title=g("\xa9nam"), album=g("\xa9alb"), albumartist=g("aART"))
        texts += [str(v) for v in t.get("\xa9lyr", [])]
    elif "ASF" in kind:                                               # wma
        def g(k):
            v = t.get(k)
            return str(v[0]) if v else ""
        info.update(artist=g("Author"), title=g("Title"), album=g("WM/AlbumTitle"), albumartist=g("WM/AlbumArtist"))
        texts += [str(v) for v in t.get("WM/Lyrics", [])]
    else:                                                             # flac, ogg, opus (Vorbis comments), ape, wv
        low = {}
        try:
            for k in t.keys():
                v = t[k]
                low[str(k).lower()] = [str(x) for x in v] if isinstance(v, (list, tuple)) else [str(v)]
        except Exception:
            low = {}
        def g(*ks):
            for k in ks:
                if low.get(k):
                    return low[k][0]
            return ""
        info.update(artist=g("artist"), title=g("title"), album=g("album"), albumartist=g("albumartist", "album artist"))
        for k in ("lyrics", "unsyncedlyrics", "syncedlyrics"):
            texts += low.get(k, [])
    return info, [x for x in texts if isinstance(x, str) and x.strip()]


NAME_RE = re.compile(r"^\s*(?:\d{1,3}\s*[-._]\s*)?(?P<artist>.+?)\s+-\s+(?P<title>.+?)\s*$")


def from_file_name(name):
    """'07 - Artist - Title' -> (artist, title), or ('', '')."""
    m = NAME_RE.match(name)
    return (m.group("artist").strip(), m.group("title").strip()) if m else ("", "")


def read_song(path, rel, size):
    """Everything known about one song without listening to it."""
    from . import audio
    s = Song(path=path, rel=rel, size=size)
    base = s.base
    for ext in WORD_SIDECARS:
        if os.path.exists(base + ext):
            s.skip = "already has a %s lyrics file" % ext
            s.tier = WORD
            return s
    if os.path.exists(base + ".elrc"):
        s.tier = WORD
        s.skip = "already has word-by-word lyrics" if classify(_read_text(base + ".elrc")) == WORD else "already has an .elrc file"
        return s
    texts = []
    try:
        info, emb = embedded(path)
        texts += [("inside the song file", t) for t in emb]
        s.seconds, s.artist, s.title = info.get("seconds", 0.0), info.get("artist", ""), info.get("title", "")
        s.album, s.albumartist = info.get("album", ""), info.get("albumartist", "")
    except Exception:
        secs, meta = audio.probe(path)      # the tag reader gave up (wrongly named or unusual file)
        s.seconds = secs or 0.0
        s.artist, s.title = meta.get("artist", ""), meta.get("title", "")
        s.album, s.albumartist = meta.get("album", ""), meta.get("album_artist", "")
        texts += [("inside the song file", v) for k, v in meta.items() if k.startswith("lyrics") and v.strip()]
    if s.artist.strip() and s.title.strip():
        s.tags_from = "tags"
    else:
        a, t = from_file_name(s.name)
        if a and t:
            s.artist, s.title, s.tags_from = s.artist.strip() or a, s.title.strip() or t, "file name"
    s.has_lrc = os.path.exists(base + ".lrc")
    if s.has_lrc:
        texts.insert(0, (os.path.basename(base) + ".lrc", _read_text(base + ".lrc")))
    if os.path.exists(base + ".txt"):
        texts.append((os.path.basename(base) + ".txt", _read_text(base + ".txt")))
    best = None
    for where, txt in texts:                 # the best kind wins; first found wins a tie, then the longer text
        tier = classify(txt)
        if best is None or tier > best[0] or (tier == best[0] and where == best[1] and len(txt) > len(best[2])):
            best = (tier, where, txt)
    if best:
        s.tier, s.lyrics_from, s.text = best
    if s.tier == WORD:
        s.skip = "already has word-by-word lyrics"
    return s


def group_same_name(songs):
    """Two files 'Song.mp3' and 'Song.flac' in one folder would share one lyrics file: only the first is handled."""
    seen = {}
    for s in songs:
        key = os.path.normcase(s.base)
        if key in seen and not s.skip:
            s.skip = "another file with the same name (%s) gets the lyrics file" % os.path.basename(seen[key].path)
        else:
            seen.setdefault(key, s)
