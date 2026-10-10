"""Replace explicitly flagged lyric sources only when accepted word timing can be published."""
import hashlib
import json
import os
from pathlib import Path
import uuid

from . import backup,files,library as L


def move_new(source,target):
    if os.path.lexists(target):raise OSError("A lyric archive/restoration target already exists; it was left alone")
    if os.name=="nt":os.rename(source,target)  # Windows refuses an existing target.
    else:
        os.link(source,target)
        os.unlink(source)


class Publication:
    def __init__(self,run,song,data):
        self.run,self.song,self.data=run,song,data
        self.moved=[];self.committed=False
        self.output_identity=None
        self.row={"song":song.rel,"elrc_sha256":hashlib.sha256(data).hexdigest(),
                  "original_elrc_sha256":song.retime_sha256,"sidecars":[]}
        self.candidate=Path(run.run_dir)/"repair candidates"/(uuid.uuid4().hex+".elrc")

    def journal(self,phase):
        target=Path(self.run.run_dir)/"lyric-repairs.jsonl"
        if target.is_symlink() or (target.exists() and target.stat().st_nlink>1):raise OSError("Linked repair journal is protected")
        with target.open("a",encoding="utf-8") as f:
            f.write(json.dumps(dict(self.row,phase=phase),ensure_ascii=False)+"\n")
            f.flush();os.fsync(f.fileno())

    def __enter__(self):
        if not self.run.backup_rec or not self.run.backup_rec.get("verified_byte_for_byte"):
            raise OSError("Wrong-lyrics repair requires a verified backup")
        for ext in L.WORD_SIDECARS:
            path=self.song.base+ext
            expected=self.song.superseded.get(ext)
            if os.path.lexists(path)!=bool(expected):raise OSError("Preferred lyric files changed during the run; retry this song")
            if not expected:continue
            saved=os.path.join(self.run.backup_rec["path"],os.path.splitext(self.song.rel)[0]+ext)
            if os.path.islink(path) or os.stat(path).st_nlink>1 or files.sha256(path)!=expected or files.sha256(saved)!=expected:
                raise OSError("Preferred lyric file or backup changed; original lyrics were preserved")
            rel=os.path.relpath(path,self.run.source)
            self.row["sidecars"].append({"rel":rel,"inactive_rel":rel+".wordlyrics-inactive-"+uuid.uuid4().hex+".bak","sha256":expected})
        self.candidate.parent.mkdir(parents=True,exist_ok=True)
        if not files.create_new(self.candidate,self.data):raise OSError("Repair candidate already exists")
        self.journal("prepared")  # recovery information survives a crash between file moves
        try:
            for row in self.row["sidecars"]:
                source=os.path.join(self.run.source,row["rel"])
                if files.sha256(source)!=row["sha256"]:raise OSError("Preferred lyrics changed before publication")
                move_new(source,os.path.join(self.run.source,row["inactive_rel"]))
                self.moved.append(row)
                if files.sha256(os.path.join(self.run.source,row["inactive_rel"]))!=row["sha256"]:raise OSError("Archived lyrics did not verify")
        except Exception:
            self.rollback();raise
        return self

    def commit(self):
        if files.sha256(self.song.base+".elrc")!=self.row["elrc_sha256"]:raise OSError("New word timing did not verify")
        self.journal("published")
        self.committed=True
        if not hasattr(self.run,"lyric_archives"):self.run.lyric_archives=[]
        self.run.lyric_archives.extend(self.moved)

    def rollback(self):
        # Restore preferred files first, so the player retains the original displayed lyrics.
        for row in reversed(self.moved):
            target=os.path.join(self.run.source,row["rel"])
            inactive=os.path.join(self.run.source,row["inactive_rel"])
            if os.path.lexists(target):raise OSError("Lyrics changed during repair; originals remain in their .wordlyrics-inactive archive")
            move_new(inactive,target)
        self.moved=[]
        target=self.song.base+".elrc"
        if self.output_identity is not None and files._same_file(target,self.output_identity) and files.sha256(target)==self.row["elrc_sha256"]:
            if self.song.retime_sha256:
                saved=os.path.join(self.run.backup_rec["path"],os.path.splitext(self.song.rel)[0]+".elrc")
                if files.sha256(saved)!=self.song.retime_sha256:raise OSError("Original timing backup changed; use the saved report for recovery")
                files.replace_backed_up(target,Path(saved).read_bytes(),self.candidate,self.row["elrc_sha256"])
                if not hasattr(self.run,"lyric_rollbacks"):self.run.lyric_rollbacks={}
                self.run.lyric_rollbacks[os.path.splitext(self.song.rel)[0]+".elrc"]=self.song.retime_sha256
            elif os.stat(target).st_nlink==1:
                os.unlink(target)  # only the matching output from this explicitly approved repair
            rel=os.path.splitext(self.song.rel)[0]+".elrc"
            self.run.written[:]=[row for row in self.run.written if row["rel"]!=rel or row["sha256"]!=self.row["elrc_sha256"]]

    def __exit__(self,kind,error,trace):
        if not self.committed:self.rollback()


def records(home,source):
    from .pipeline import library_home
    result=[];seen=set()
    for journal in sorted(Path(library_home(home,source)).glob("run */lyric-repairs.jsonl"),reverse=True):
        if journal.is_symlink() or journal.stat().st_size>2_000_000:continue
        try:
            for line in journal.read_text(encoding="utf-8").splitlines():
                row=json.loads(line)
                for item in row.get("sidecars",[]):
                    rel=item["rel"];inactive=item["inactive_rel"]
                    if rel in seen or os.path.isabs(rel) or os.path.isabs(inactive):continue
                    target=os.path.join(source,rel);saved=os.path.join(source,inactive)
                    if not rel.lower().endswith(L.WORD_SIDECARS) or not inactive.startswith(rel+".wordlyrics-inactive-") or not inactive.endswith(".bak"):continue
                    if os.path.dirname(rel)!=os.path.dirname(inactive) or not backup.inside(target,source) or not backup.inside(saved,source):continue
                    if not os.path.lexists(target) and os.path.isfile(saved) and not os.path.islink(saved):
                        result.append(item);seen.add(rel)
        except (OSError,ValueError,TypeError,KeyError,AttributeError):continue
    return result


def restore(source,row):
    # Recheck journal rows and content just before restoration; never overwrite a user's later edit.
    rel=row.get("rel","");inactive=row.get("inactive_rel","")
    target=os.path.join(source,rel);saved=os.path.join(source,inactive)
    if not rel.lower().endswith(L.WORD_SIDECARS) or not backup.inside(target,source) or not backup.inside(saved,source) or os.path.dirname(rel)!=os.path.dirname(inactive) or not inactive.startswith(rel+".wordlyrics-inactive-") or not inactive.endswith(".bak"):
        raise OSError("Invalid archived lyric record")
    if os.path.islink(saved) or os.stat(saved).st_nlink>1 or files.sha256(saved)!=row.get("sha256"):
        raise OSError("Archived lyrics changed; they were left alone")
    move_new(saved,target)
