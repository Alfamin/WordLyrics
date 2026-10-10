"""Private editable lyric drafts. Editing never changes a song or its active sidecars."""
import os
from pathlib import Path
import subprocess
import uuid

from . import files,library as L
from .guides import WORD


def create(home,song,text=None,label="custom"):
    if text is None:
        current=L.read_song(song.path,song.rel,os.path.getsize(song.path),redo=True,
                            retime_mode="current",repair_lyrics=True,ignore_source=True)
        text=current.text
    folder=Path(home)/"lyric drafts";folder.mkdir(parents=True,exist_ok=True)
    if folder.is_symlink() or L._is_junction(str(folder)):raise OSError("Linked lyric-draft folders are protected")
    path=folder/(label+"-"+uuid.uuid4().hex+".txt")
    clean="\n".join(line.strip() for line in WORD.sub("",text).splitlines() if line.strip())
    if not files.create_new(path,(clean+"\n").encode("utf-8")):raise OSError("Draft already exists")
    return str(path)


def open_editor(path):
    if os.name!="nt":raise OSError("Open the displayed draft path in your text editor, save it, then choose Use saved draft")
    editor=Path(os.environ.get("SystemRoot",r"C:\Windows"))/"System32"/"notepad.exe"
    subprocess.Popen([str(editor),path],shell=False)  # paths remain arguments, never commands


def lrclib_results(home,song,query):
    from .fetch import Client,judge,_usable,Found,artist_tokens,title_variants,sim,artist_ok,norm
    client=Client(str(Path(home)/"lyric drafts"/"source cache"),refresh=True)
    query=query.strip()[:256]
    target_query=not query or bool(song.title and (sim(song.title,query)>=.92 or norm(song.title) in norm(query)))
    requests=[]
    if target_query and song.artist and song.title:
        primary=(artist_tokens(song.artist) or [song.artist])[0]
        title=title_variants(song.title)[0]
        if song.seconds>0:
            params={"artist_name":primary,"track_name":title,"duration":int(round(song.seconds))}
            if song.album:params["album_name"]=song.album
            requests.append(("/get",params))
        requests.append(("/search",{"artist_name":primary,"track_name":title}))
    else:requests.append(("/search",{"q":query or song.title or song.name}))
    data=[];errors=[]
    for endpoint,params in requests:
        status,result=client.get(endpoint,params)
        if status=="error":errors.append(str(result));continue
        if status=="ok":
            if isinstance(result,list):data.extend(result[:100])
            elif isinstance(result,dict):data.append(result)
    if not data and errors:raise OSError(errors[0])
    rows=[];seen=set()
    for row in data:
        if not isinstance(row,dict):continue
        if any(not isinstance(row.get(key,""),str) for key in ("artistName","trackName","albumName")):continue
        identity=str(row.get("id")) if row.get("id") else str((row.get("artistName"),row.get("trackName"),row.get("albumName"),row.get("duration")))
        if identity in seen:continue
        found=_usable(row,song)
        if not isinstance(found,Found):continue  # instrumental/empty/censored sources never become an override
        seen.add(identity)
        ok,reasons,title_score,off=judge(song,row.get("artistName"),row.get("trackName"),row.get("duration"))
        same=artist_ok(song.artist,song.albumartist,row.get("artistName")) and title_score>=.92
        near=same and off is not None and off<=10 and not any("another version" in reason for reason in reasons)
        rank=(0 if ok else 1 if near else 2 if same else 3,
              0 if found.tier==L.LINE else 1,off if off is not None else float('inf'),
              -sim(song.album,row.get("albumName")),str(row.get("id") or ""))
        rows.append(dict(row,chosen_text=found.text,warning="" if ok else ", ".join(reasons),
                         seconds_off=off,recording_match=ok,near_match=near,
                         synced=found.tier==L.LINE,_rank=rank))
    return sorted(rows,key=lambda row:row["_rank"])
