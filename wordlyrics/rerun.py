"""Guarded restoration of explicitly retimed lyrics. No audio is ever written."""
import hashlib
import json
import os
from pathlib import Path
import uuid

from . import backup,files
from .pipeline import library_home


def records(home,source):
    latest={}
    for journal in sorted(Path(library_home(home,source)).glob("run */retimed.jsonl")):
        try:
            with journal.open(encoding="utf-8") as f:
                for line in f:
                    row=json.loads(line)
                    if not isinstance(row,dict):continue
                    rel=row.get("rel")
                    hashes=(row.get("sha256"),row.get("previous_sha256"))
                    valid=all(isinstance(h,str) and len(h)==64 and all(c in "0123456789abcdef" for c in h) for h in hashes)
                    if valid and isinstance(row.get("backup"),str) and row["backup"] and isinstance(rel,str) and rel.lower().endswith(".elrc") and not os.path.isabs(rel):
                        target=os.path.join(source,rel)
                        if backup.inside(target,source):
                            latest[rel]=row
        except (OSError,ValueError,TypeError):
            continue
    return list(latest.values())


def restore(home,source,row):
    if not isinstance(row,dict) or not all(isinstance(row.get(k),str) for k in ("rel","backup","sha256","previous_sha256")):
        raise OSError("The restoration record is incomplete")
    rel=row["rel"]
    target=os.path.join(source,rel)
    saved=row["backup"]
    if not backup.inside(target,source) or not rel.lower().endswith(".elrc") or os.path.islink(target) or os.path.islink(saved):
        raise OSError("The restoration record does not identify a safe lyric file")
    if os.path.basename(saved)!=os.path.basename(target):
        raise OSError("The backup does not match this song")
    with open(saved,"rb") as f:
        previous=f.read()
    if hashlib.sha256(previous).hexdigest()!=row["previous_sha256"]:
        raise OSError("The previous lyric backup did not verify")
    if not os.path.lexists(target):
        if not files.create_new(target,previous):
            raise OSError("A lyric file appeared before restoration")
        return
    if files.sha256(target)!=row["sha256"]:
        raise OSError("The current lyrics changed after retiming; they were left alone")
    archive=Path(library_home(home,source))/"restores"/uuid.uuid4().hex/rel
    archive.parent.mkdir(parents=True)
    with open(target,"rb") as f:
        current=f.read()
    if hashlib.sha256(current).hexdigest()!=row["sha256"]:
        raise OSError("The current lyrics changed before restoration")
    if not files.create_new(archive,current):
        raise OSError("The restoration archive already exists")
    files.replace_backed_up(target,previous,archive,row["sha256"])
