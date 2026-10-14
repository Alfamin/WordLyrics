"""Recover an explicit lyric source after a move; never infer one from song names."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from .sources import genius_song_url

HOME = Path(__file__).resolve().parents[1]
MAX_AUDIO_HASH = 1_000_000_000
SKIPPED = {}        # record file name -> why recover() ignored it; the menu says so once after it reads the library


def identity(path):
    st = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(st.st_mode):
        raise OSError("A linked or non-regular song cannot identify a source choice")
    return [str(st.st_dev), str(st.st_ino), str(getattr(st, "st_birthtime_ns", 0))]


def digest(path):
    before = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_AUDIO_HASH:
        return ""
    with open(path, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise OSError("Song changed while remembering its source")
        result = hashlib.file_digest(stream, "sha256").hexdigest()
    after = os.stat(path, follow_symlinks=False)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise OSError("Song changed while remembering its source")
    return result


def read_pin(path):
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1 or st.st_size > 4096:
        raise OSError("This source-choice file needs manual inspection")
    raw = Path(path).read_bytes()
    if len(raw)>4096:
        raise OSError("Source choice changed while reading it")
    value = raw.decode("utf-8-sig").strip()
    source = "auto" if value.casefold() == "auto" else genius_song_url(value)
    if not source:
        raise OSError("Invalid saved source choice")
    return source, hashlib.sha256(raw).hexdigest()


def remember(audio, pin, home=None):
    """Private recovery record only. The original song and source file stay unchanged."""
    source, pin_hash = read_pin(pin)
    audio_id = identity(audio)
    folder = Path(home or HOME) / "source choices"
    folder.mkdir(parents=True, exist_ok=True)
    if folder.is_symlink():
        raise OSError("Linked source-choice storage was preserved")
    key = hashlib.sha256(os.path.normcase(os.path.abspath(pin)).encode("utf-8")).hexdigest()
    target = folder / (key + ".json")
    if target.exists():
        st = target.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
            raise OSError("Linked source-choice record was preserved")
        if st.st_size <= 8192:
            try:
                previous=json.loads(target.read_text(encoding="utf-8"))
                current=os.stat(audio,follow_symlinks=False)
                if (previous.get("version"),previous.get("pin_hash"),previous.get("identity"),previous.get("size"),previous.get("mtime")) == (1,pin_hash,audio_id,current.st_size,current.st_mtime_ns):
                    return
            except (ValueError,AttributeError):pass
    payload = {"version": 1, "audio": os.path.abspath(audio), "pin": os.path.abspath(pin),
               "source": source, "pin_hash": pin_hash, "identity": audio_id,
               "size": os.path.getsize(audio), "mtime": os.stat(audio).st_mtime_ns, "sha256": digest(audio)}
    if identity(audio) != audio_id:
        raise OSError("Song moved or was replaced while remembering its source")
    fd, temporary = tempfile.mkstemp(prefix=".source-", suffix=".tmp", dir=folder)
    temporary_id=None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            own=os.fstat(stream.fileno());temporary_id=(own.st_dev,own.st_ino)
            json.dump(payload, stream, ensure_ascii=False)
            stream.flush(); os.fsync(stream.fileno())
        if target.is_symlink() or (target.exists() and target.stat().st_nlink > 1):
            raise OSError("Source-choice record changed; preserved")
        os.replace(temporary, target)
    finally:
        from .files import _same_file
        if temporary_id is not None and _same_file(temporary,temporary_id):
            os.unlink(temporary)


def recover(audio, home=None):
    """Match a file identity, or exact bytes across drives. Returns (source, reason)."""
    folder = Path(home or HOME) / "source choices"
    if not folder.exists():
        return "", ""
    if folder.is_symlink():
        return "", "SOURCE_RECOVERY_INVALID: linked source-choice storage was preserved"
    try:
        paths = sorted(folder.glob("*.json"))
        if len(paths) > 4096:
            SKIPPED["*"] = "more than 4096 records; the rest were not checked"
            paths = paths[:4096]
        current_id, size = identity(audio), os.path.getsize(audio)
        entries = []
        own_problem = False
        for path in paths:
            entry = None
            try:
                st = path.lstat()
                if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1 or st.st_size > 8192:
                    raise ValueError("unsafe source-choice record")
                entry = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(entry, dict) or entry.get("version") != 1 or not isinstance(entry.get("identity"), list):
                    raise ValueError("invalid source-choice record")
                try:
                    source, pin_hash = read_pin(entry["pin"])
                except FileNotFoundError:
                    continue
                if pin_hash == entry.get("pin_hash") and source == entry.get("source"):
                    entries.append(entry)
            except (OSError, ValueError, KeyError, TypeError, UnicodeError) as error:
                # one damaged record must not stop every other song: leave it where it is, skip it, say so once
                SKIPPED[path.name] = type(error).__name__
                if isinstance(entry, dict) and entry.get("identity") == current_id:
                    own_problem = True       # ... unless it is this very recording's own choice
        matches = [entry for entry in entries if entry["identity"] == current_id]
        if not matches:
            candidates = [entry for entry in entries if entry.get("size") == size and entry.get("sha256")]
            if candidates:
                actual = digest(audio)
                matches = [entry for entry in candidates if entry["sha256"] == actual]
        sources = {entry["source"] for entry in matches}
        if len(sources) > 1:
            return "", "SOURCE_RECOVERY_CONFLICT: this recording has conflicting saved sources; choose its source again"
        if not sources and own_problem:
            raise ValueError("this recording's saved source record is damaged")
        return (sources.pop(), "") if sources else ("", "")
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        return "", "SOURCE_RECOVERY_INVALID: saved source recovery needs inspection; choose this song's source again"
