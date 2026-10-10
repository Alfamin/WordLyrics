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
    if not files.create_new(path,(WORD.sub("",text).strip()+"\n").encode("utf-8")):raise OSError("Draft already exists")
    return str(path)


def open_editor(path):
    if os.name!="nt":raise OSError("Open the displayed draft path in your text editor, save it, then choose Use saved draft")
    editor=Path(os.environ.get("SystemRoot",r"C:\Windows"))/"System32"/"notepad.exe"
    subprocess.Popen([str(editor),path],shell=False)  # paths remain arguments, never commands


def lrclib_results(home,song,query):
    from .fetch import Client,judge,_usable,Found
    client=Client(str(Path(home)/"lyric drafts"/"source cache"),refresh=True)
    status,data=client.get("/search",{"q":query[:256]})
    if status=="error":raise OSError(str(data))
    if status!="ok" or not isinstance(data,list):return []
    rows=[]
    for row in data[:100]:
        if not isinstance(row,dict):continue
        found=_usable(row,song)
        if not isinstance(found,Found):continue  # instrumental/empty/censored sources never become an override
        ok,reasons,_,_=judge(song,row.get("artistName"),row.get("trackName"),row.get("duration"))
        rows.append(dict(row,chosen_text=found.text,warning="" if ok else ", ".join(reasons)))
    return rows
