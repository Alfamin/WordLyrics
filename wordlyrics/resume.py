"""Durable jobs and retries of affected songs only. Contains paths/options, never lyric text."""
import json
import os
from pathlib import Path
import uuid

from . import backup, files, library as L

FLAGS={"offline":"--offline","no_lrc":"--no-lrc","quick_backup":"--quick-backup","redo":"--redo"}


def save(run, paths=None):
    options={key:getattr(run.opts,key,False) for key in FLAGS}
    options.update(speed=run.opts.speed,backup_to=run.opts.backup_to,retime_mode=getattr(run.opts,"retime_mode","current"),lyrics_file=getattr(run.opts,"lyrics_file",None))
    target=Path(run.run_dir)/"request.json"
    if paths is None and target.exists():
        old=json.loads(target.read_text(encoding="utf-8"));paths=old.get("songs")
    elif paths is None:paths=run.only_songs
    data={"schema":1,"source":run.source,"songs":paths,"options":options,
          "initial_elrc_hashes":{s.path:s.retime_sha256 for s in run.songs if s.retime_sha256},
          "unreadable":[os.path.abspath(os.path.join(run.source,p)) for p in getattr(run,"unreadable",[])]}
    temporary=target.with_suffix(".tmp")
    if target.is_symlink() or temporary.is_symlink():raise OSError("Linked job state is protected")
    temporary.write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
    os.replace(temporary,target)


def read(home,run_dir):
    folder=Path(run_dir).resolve()
    if not backup.inside(str(folder),str(Path(home)/"runs")) or folder.is_symlink():raise ValueError("Choose a saved WordLyrics run")
    target=folder/"request.json"
    if target.is_symlink():raise ValueError("Linked job state is protected")
    with target.open("rb") as f:raw=f.read(8_000_001)
    if len(raw)>8_000_000:raise ValueError("Job state is too large")
    data=json.loads(raw)
    if data.get("schema")!=1 or not isinstance(data.get("source"),str) or not isinstance(data.get("options"),dict):raise ValueError("Invalid saved job")
    source=os.path.abspath(data["source"])
    if os.path.dirname(source)==source or os.path.islink(source) or L._is_junction(source):raise ValueError("Protected music folder")
    paths=data.get("songs")
    if paths is None:
        unreadable=[]
        entries,_=L.walk(source,unreadable=unreadable)
        paths=[v[0] for v in entries if Path(v[0]).suffix.lower() in L.AUDIO_EXT]
        if not paths and (unreadable or not os.path.isdir(source)):
            data["needs_scan"]=True
    if not isinstance(paths,list) or any(not isinstance(p,str) or not backup.inside(p,source) or Path(p).suffix.lower() not in L.AUDIO_EXT or os.path.islink(p) for p in paths):
        raise ValueError("Saved job includes a path outside its music folder")
    blocked=[]
    for path in data.get("unreadable",[]):
        if not isinstance(path,str) or not backup.inside(path,source) or os.path.islink(path) or L._is_junction(path):raise ValueError("Protected unreadable target")
        if Path(path).suffix.lower() in L.AUDIO_EXT and not os.path.isdir(path):paths.append(path);continue
        denied=[]
        entries,_=L.walk(path,unreadable=denied)
        paths.extend(row[0] for row in entries if Path(row[0]).suffix.lower() in L.AUDIO_EXT)
        if denied or not os.path.isdir(path):blocked.append(path)
    data["blocked_folders"]=blocked
    return folder,data,paths


def pending(home,run_dir):
    folder,data,paths=read(home,run_dir)
    if data.get("needs_scan"):
        reason="The music folder cannot be read or the drive is disconnected. Fix access/reconnect it; existing word timestamps will be kept."
        return data,[{"path":data["source"],"reason":reason,"status":"needs_scan","folder":True}]
    done=set()
    latest={}
    log=folder/"log.jsonl"
    if log.exists() and not log.is_symlink():
        with log.open(encoding="utf-8") as f:
            for line in f:
                try:
                    row=json.loads(line)
                    if isinstance(row.get("song"),str):latest[os.path.join(data["source"],row["song"])]=row
                except (ValueError,AttributeError):continue
    journal=folder/"resumed.jsonl"
    if journal.exists() and not journal.is_symlink():
        with journal.open(encoding="utf-8") as f:
            for line in f:
                try:
                    row=json.loads(line)
                    if isinstance(row.get("path"),str):latest[row["path"]]=row
                    if row.get("status")=="timed":done.add(row.get("path"))
                except (ValueError,AttributeError):continue
    result=[{"path":p,"reason":"This folder could not be scanned. Fix access/reconnect it, then continue; finished songs stay finished.","status":"needs_scan","folder":True} for p in data.get("blocked_folders",[])]
    for path in dict.fromkeys(paths):
        row=latest.get(path,{})
        try:os.stat(path)
        except FileNotFoundError:
            result.append({"path":path,"reason":"SONG_MISSING: reconnect the drive or restore this file, then resume.","status":"not_reached"});continue
        except PermissionError:
            result.append({"path":path,"reason":"PERMISSION_DENIED: this file cannot be read by the current Windows user.","status":"failed"});continue
        current=Path(path).with_suffix(".elrc")
        verified=current.is_file() and not current.is_symlink() and L.classify(L._read_text(str(current)))==L.WORD
        initial=data.get("initial_elrc_hashes",{}).get(path)
        if verified and initial and files.sha256(current)!=initial:done.add(path)
        if verified and (path in done or row.get("status")=="timed" or not data["options"].get("redo")):
            continue
        if row.get("status")=="skipped" and "protected" in row.get("reason",""):continue
        result.append({"path":path,"reason":row.get("reason","Not reached before the job stopped."),"status":row.get("status","not_reached")})
    return data,result


def submit(home,run_dir,execute,*,fresh=False,backup_to=None):
    data,rows=pending(home,run_dir)
    if not rows:return 0
    if any(row.get("folder") for row in rows) and not data.get("needs_scan"):
        rows=[row for row in rows if not row.get("folder")]
        if not rows:return 4  # fix inaccessible subfolders first; do not broaden to the whole library
    folder=Path(home)/"selections";folder.mkdir(parents=True,exist_ok=True)
    selected=folder/("resume-"+uuid.uuid4().hex+".txt")
    files.create_new(selected,("\n".join(row["path"] for row in rows if not row.get("folder"))+"\n").encode("utf-8"))
    result=selected.with_suffix(".result.json")
    options=data["options"]
    args=[data["source"],"--yes","--retry","--no-open","--result",str(result),"--speed",options.get("speed","full")]
    if not data.get("needs_scan"):args += ["--songs-from",str(selected)]
    for key,flag in FLAGS.items():
        if options.get(key):args.append(flag)
    place=backup_to if backup_to is not None else options.get("backup_to")
    if place:args += ["--backup-to",place]
    if options.get("redo"):args += ["--confirm-redo","--retime-mode","fresh" if fresh else options.get("retime_mode","current")]
    if options.get("lyrics_file") and not fresh:args += ["--lyrics-file",options["lyrics_file"]]
    code=execute(args)
    try:
        output=json.loads(result.read_text(encoding="utf-8"))
        permitted={row["path"] for row in rows}
        journal_path=Path(run_dir)/"resumed.jsonl"
        if journal_path.is_symlink() or (journal_path.exists() and journal_path.stat().st_nlink>1):raise OSError("Protected recovery journal")
        with journal_path.open("a",encoding="utf-8") as journal:
            for song in output.get("songs",[]):
                if song.get("path") in permitted:
                    journal.write(json.dumps({"path":song["path"],"status":song.get("status","not_reached"),"reason":song.get("reason","")})+"\n")
            journal.flush();os.fsync(journal.fileno())
    except (OSError,ValueError):pass
    return code
