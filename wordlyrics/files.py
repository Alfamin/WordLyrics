"""Publish complete new files without overwriting anything, including racing writers."""
import os
import hashlib
import stat
import tempfile


def _same_file(path, identity):
    try:
        current = os.lstat(path)
        return stat.S_ISREG(current.st_mode) and (current.st_dev,current.st_ino) == identity
    except FileNotFoundError:
        return False


def _publish(staging, target):
    if os.name == "nt":
        os.rename(staging, target)   # Windows rename refuses an existing destination, atomically.
    else:
        os.link(staging, target)     # POSIX rename can overwrite: a new hard link is exclusive instead.


def create_new(path, data):
    """-> False if destination exists; otherwise stage, verify, and publish the complete bytes.

    Only the unique temporary file created by this call can be cleaned up. Existing destinations,
    links, and other programs' temporary files are never removed or replaced.
    """
    target = os.path.abspath(os.fspath(path))
    if os.path.lexists(target):
        return False
    fd, staging = tempfile.mkstemp(prefix=".wordlyrics-", suffix=".tmp", dir=os.path.dirname(target))
    identity = None
    try:
        with os.fdopen(fd,"wb") as f:
            original = os.fstat(f.fileno())
            identity = (original.st_dev,original.st_ino)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if not _same_file(staging,identity):
            raise OSError("Temporary lyric file changed before publication")
        with open(staging,"rb") as f:
            opened = os.fstat(f.fileno())
            if (opened.st_dev,opened.st_ino) != identity or f.read() != data:
                raise OSError("Temporary lyric file did not verify")
        if not _same_file(staging,identity):
            raise OSError("Temporary lyric file changed before publication")
        try:
            _publish(staging,target)
        except FileExistsError:
            return False
        return True
    finally:
        if identity is not None and _same_file(staging,identity):
            os.unlink(staging)       # This call's own staged file only; never glob or delete destinations.


def sha256(path):
    with open(path,"rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def replace_backed_up(path,data,backup,expected):
    """Explicit replacement of a lyric/source file only, after an independent backup verifies."""
    target=os.path.abspath(os.fspath(path))
    saved=os.path.abspath(os.fspath(backup))
    if not target.lower().endswith((".elrc",".lyrics-source.txt")):
        raise OSError("Only lyric timing or source-choice files may be replaced")
    if os.path.realpath(target)==os.path.realpath(saved) or os.path.islink(target) or os.path.islink(saved):
        raise OSError("Replacement needs a separate regular-file backup")
    if sha256(saved)!=expected or sha256(target)!=expected:
        raise OSError("Lyrics or backup changed; the existing file was preserved")
    fd,staging=tempfile.mkstemp(prefix=".wordlyrics-",suffix=".tmp",dir=os.path.dirname(target))
    identity=None
    try:
        with os.fdopen(fd,"wb") as f:
            st=os.fstat(f.fileno())
            identity=(st.st_dev,st.st_ino)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if not _same_file(staging,identity) or sha256(staging)!=hashlib.sha256(data).hexdigest():
            raise OSError("The replacement lyric file did not verify")
        if os.path.islink(target) or sha256(target)!=expected or sha256(saved)!=expected:
            raise OSError("Lyrics changed during the rerun; the existing file was preserved")
        os.replace(staging,target)
    finally:
        if identity is not None and _same_file(staging,identity):
            os.unlink(staging)
