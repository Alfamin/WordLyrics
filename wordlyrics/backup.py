"""An exact copy of the whole music folder, made before anything else happens.

  * Every file is copied (not only songs), with its folder structure and dates, and read back to
    check that the copy is byte-for-byte the same.
  * Nothing in the music folder is changed, and nothing in an older backup is ever changed or removed.
  * Each run gets its own complete backup folder. A file that has not changed since the previous
    backup is not copied again: the new folder points at the same data on disk (a hard link), so a
    second backup of a large library takes seconds and almost no extra space.
  * The copy is only called complete when every file is there; an interrupted copy keeps the ending
    ".incomplete" and is continued next time.
  * A run that was asked to handle only certain songs copies only those songs and the files next to
    them (a "part" backup, a folder of its own that says so in its name). Full backups never build on it.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from datetime import datetime

CHUNK = 4 * 1024 * 1024
FOLDER = "WordLyrics Backups"
MARGIN = 256 * 1024 * 1024
NOTE = ".wordlyrics.json"       # one small file next to each backup folder: what it is a copy of


class BackupError(Exception):
    pass


def _long(path):
    """Windows: allow paths longer than 260 characters."""
    if os.name == "nt":
        path = os.path.abspath(path)
        if not path.startswith("\\\\?\\"):
            return "\\\\?\\UNC\\" + path[2:] if path.startswith("\\\\") else "\\\\?\\" + path
    return path


def fixed_drives():
    """[(root, free bytes, total bytes)] of the built-in drives (not USB sticks, not network drives)."""
    out = []
    if os.name == "nt":
        import ctypes
        import string
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for i, letter in enumerate(string.ascii_uppercase):
            root = letter + ":\\"
            if mask >> i & 1 and ctypes.windll.kernel32.GetDriveTypeW(root) == 3:      # DRIVE_FIXED
                try:
                    u = shutil.disk_usage(root)
                    out.append((root, u.free, u.total))
                except OSError:
                    pass
    else:
        home = os.path.expanduser("~")
        u = shutil.disk_usage(home)
        out.append((home, u.free, u.total))
    return out


def default_place(source):
    """Where the backup goes when the user does not choose: the drive with the most free space."""
    drives = fixed_drives()
    if not drives:
        return os.path.join(os.path.expanduser("~"), FOLDER)
    return os.path.join(max(drives, key=lambda d: d[1])[0], FOLDER)


def inside(child, parent):
    c, p = os.path.normcase(os.path.realpath(child)), os.path.normcase(os.path.realpath(parent))
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


def _sha(path):
    h = hashlib.sha256()
    with open(_long(path), "rb") as fh:
        for b in iter(lambda: fh.read(CHUNK), b""):
            h.update(b)
    return h.hexdigest()


def _copy(src, dst, verify):
    """Copy one file under a temporary name, check it, then give it its real name."""
    h = hashlib.sha256()
    tmp = dst + ".wlpart"
    with open(_long(src), "rb") as fi, open(_long(tmp), "wb") as fo:
        for b in iter(lambda: fi.read(CHUNK), b""):
            h.update(b)
            fo.write(b)
        fo.flush()
        os.fsync(fo.fileno())
    shutil.copystat(_long(src), _long(tmp))
    if verify and _sha(tmp) != h.hexdigest():
        raise BackupError("the copy of %s does not read back the same" % os.path.basename(src))
    os.replace(_long(tmp), _long(dst))


def _notes(place, source):
    """The backups of this music folder that exist in `place`: [(note file, record)] oldest first."""
    out = []
    try:
        names = sorted(os.listdir(place))
    except OSError:
        return out
    for n in names:
        if n.endswith(NOTE):
            try:
                rec = json.load(open(os.path.join(place, n), encoding="utf-8"))
            except Exception:
                continue
            if os.path.normcase(rec.get("source", "")) == os.path.normcase(source) and os.path.isdir(os.path.join(place, rec.get("folder", "?"))):
                out.append((os.path.join(place, n), rec))
    return out


def make(source, place, files, links=(), progress=None, verify=True, stop=None, part=False):
    """files: [(path, rel, size, mtime_ns)] from library.walk. -> record of the backup; record['files'] is
    {rel: [size, mtime_ns]} exactly as copied. progress(done_bytes, total_bytes, rel) is called as it goes.
    part: `files` is not the whole folder but a few songs (library.walk_songs)."""
    source, place = os.path.abspath(source), os.path.abspath(place)
    if inside(place, source):
        raise BackupError("the backup cannot be inside the music folder itself (%s); choose another place with --backup-to" % place)
    try:
        os.makedirs(place, exist_ok=True)
    except OSError as e:
        raise BackupError("the backup folder %s cannot be created (%s); choose another place with --backup-to" % (place, e.strerror or e))
    notes = _notes(place, source)
    done_ones = [r for _, r in notes if r.get("complete") and not r.get("part")]
    prev_dir = os.path.join(place, done_ones[-1]["folder"]) if done_ones else None
    prev_files = done_ones[-1].get("files", {}) if done_ones else {}
    unfinished = [(p, r) for p, r in notes if not r.get("complete") and bool(r.get("part")) == part]
    if unfinished:      # an interrupted copy is continued, unless it holds files the music folder no longer has
        now = {f[1] for f in files} | {f[1] + ".wlpart" for f in files}
        old = os.path.join(place, unfinished[-1][1]["folder"])
        if any(os.path.relpath(os.path.join(d, f), old) not in now for d, _, fs in os.walk(old) for f in fs):
            unfinished = []
    if unfinished:
        note_path, rec0 = unfinished[-1]
        target = os.path.join(place, rec0["folder"])
    else:
        name = os.path.basename(source.rstrip("\\/")) or "music"
        only = " (single songs)" if part else ""
        target = os.path.join(place, "%s %s%s.incomplete" % (name, datetime.now().strftime("%Y-%m-%d %H.%M.%S.%f"), only))
        os.makedirs(target)
        note_path = target + NOTE
        with open(note_path, "w", encoding="utf-8") as fh:
            json.dump({"source": source, "folder": os.path.basename(target), "complete": False, "part": part}, fh)
    total = sum(f[2] for f in files)
    todo = []
    for path, rel, size, mtime in files:
        try:
            st = os.stat(_long(os.path.join(target, rel)))
            if st.st_size == size and st.st_mtime_ns == mtime:
                continue                                        # already there from the interrupted copy
        except OSError:
            pass
        todo.append((path, rel, size, mtime))
    need = sum(t[2] for t in todo if prev_files.get(t[1]) != [t[2], t[3]])
    free = shutil.disk_usage(place).free
    if need + MARGIN > free:
        raise BackupError("not enough room for the backup in %s (%.1f GB needed, %.1f GB free)" % (place, need / 1e9, free / 1e9))
    for dirpath, dirnames, _ in os.walk(source) if not part else ():    # the folder structure, empty folders included
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        os.makedirs(_long(os.path.join(target, os.path.relpath(dirpath, source))), exist_ok=True)
    copied_as = {rel: [size, mtime] for _, rel, size, mtime in files}
    done = total - sum(t[2] for t in todo)
    copied = copied_bytes = linked = 0
    vanished = []
    t0 = time.time()
    for path, rel, size, mtime in todo:
        if stop is not None and stop.is_set():
            raise BackupError("stopped before the backup was complete")
        if progress:
            progress(done, total, rel)
        dst = os.path.join(target, rel)
        made = False
        if part:
            os.makedirs(_long(os.path.dirname(dst)), exist_ok=True)
        if prev_dir and prev_files.get(rel) == [size, mtime]:
            try:
                old = os.path.join(prev_dir, rel)
                st = os.stat(_long(old))
                if st.st_size == size and st.st_mtime_ns == mtime:
                    os.link(_long(old), _long(dst))             # same data, no second copy on disk
                    made, linked = True, linked + 1
            except OSError:
                made = False                                    # other drive, or a file system without links: copy
        for _ in range(3) if not made else ():
            try:
                before = os.stat(_long(path))
            except OSError:
                vanished.append(rel)                            # removed by someone else since the folder was read
                copied_as.pop(rel, None)
                break
            os.makedirs(_long(os.path.dirname(dst)), exist_ok=True)
            _copy(path, dst, verify)
            after = os.stat(_long(path))
            if (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns):
                copied_as[rel] = [before.st_size, before.st_mtime_ns]
                copied, copied_bytes = copied + 1, copied_bytes + before.st_size
                break                                           # else: another program wrote to it meanwhile, copy again
        else:
            if not made:
                raise BackupError("%s keeps changing while it is being copied (is another program writing to it?)" % rel)
        done += size
    if progress:
        progress(total, total, "")
    for rel, (size, _) in copied_as.items():                    # final check: every file is there, right size
        try:
            ok = os.path.getsize(_long(os.path.join(target, rel))) == size
        except OSError:
            ok = False
        if not ok:
            raise BackupError("backup check failed for " + rel)
    final = target[: -len(".incomplete")]
    os.replace(target, final)                                   # only now is it called complete
    os.replace(note_path, final + NOTE)
    rec = {"source": source, "folder": os.path.basename(final), "made": datetime.now().isoformat(timespec="seconds"),
           "complete": True, "part": part, "files_total": len(copied_as), "bytes_total": sum(v[0] for v in copied_as.values()),
           "copied": copied, "copied_bytes": copied_bytes, "linked_to_previous_backup": linked,
           "verified_byte_for_byte": bool(verify), "seconds": round(time.time() - t0, 1),
           "links_not_followed": list(links), "vanished_during_copy": vanished, "files": copied_as}
    with open(final + NOTE, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False)
    return dict(rec, path=final)
