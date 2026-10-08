"""Numbered terminal controls. Batch work never asks for help with individual failures."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import shutil
import urllib.parse
import uuid
import webbrowser

from . import __version__,backup,files,library as L,rerun
from .pipeline import library_home
from .sources import genius_song_url
from .ui import duration

DIGITS=str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩،","01234567890123456789,")
FAILURES={"no_lyrics","not_timed","rejected","failed","not_reached"}


def selection(text,count):
    """Numbers/ranges only, stable one-based indices, no arbitrary expression evaluation."""
    text=text.translate(DIGITS).strip()
    if not text or text=="0":
        return []
    chosen=set()
    for part in text.replace(","," ").split():
        bounds=part.split("-")
        if len(bounds)>2 or not all(b.isdecimal() for b in bounds):
            raise ValueError("Use song numbers such as 1,3-5.")
        a=int(bounds[0]);b=int(bounds[-1])
        if a<1 or b<a or b>count:
            raise ValueError("A song number is outside this list.")
        chosen.update(range(a-1,b))
    return sorted(chosen)


def dropped_files(text):
    raw=text.strip()
    single=raw.strip('"')
    values=[single] if os.path.isfile(single) else [s.strip('"') for s in shlex.split(raw,posix=False)]
    paths=[]
    for value in values:
        path=os.path.abspath(os.path.expanduser(value))
        if not os.path.isfile(path) or os.path.islink(path) or Path(path).suffix.lower() not in L.AUDIO_EXT:
            raise ValueError("Drop existing audio files only; no folders or links.")
        if path not in paths:
            paths.append(path)
    return paths


def drive_root(path):
    path=os.path.abspath(path)
    return os.path.dirname(path)==path


class Menu:
    def __init__(self,home,initial,executor,reader=None,writer=None,options=None):
        self.home=Path(home)
        self.execute=executor
        self.read=reader or input
        self.say=writer or print
        self.state={"library":"","backup":"","speed":"full","offline":False}
        self.initial=[]
        self.catalog=None
        self.preferences=self.home/"menu-settings.json"
        try:
            data=json.loads(self.preferences.read_text(encoding="utf-8"))
            for key in self.state:
                if isinstance(data.get(key),type(self.state[key])):
                    self.state[key]=data[key]
        except (OSError,ValueError,AttributeError):
            pass
        if self.state["speed"] not in ("full","half","light"):
            self.state["speed"]="full"
        if options is not None:
            if options.offline:self.state["offline"]=True
            if options.speed:self.state["speed"]=options.speed
            if options.backup_to:self.state["backup"]=options.backup_to
        if len(initial)==1 and os.path.isdir(initial[0]):
            self.state["library"]=os.path.abspath(initial[0])
        elif initial:
            self.initial=[os.path.abspath(p) for p in initial if os.path.isfile(p)]
            if self.initial:
                self.state["library"]=os.path.dirname(self.initial[0])

    def ask(self,prompt):
        try:
            return self.read(prompt).strip().translate(DIGITS)
        except (EOFError,KeyboardInterrupt):
            return "0"

    def save(self):
        self.home.mkdir(parents=True,exist_ok=True)
        temp=self.home/(".menu-settings-"+uuid.uuid4().hex+".tmp")
        with temp.open("x",encoding="utf-8") as f:
            json.dump(self.state,f)
        os.replace(temp,self.preferences)

    def root(self):
        root=self.state["library"]
        if not root or not os.path.isdir(root) or os.path.islink(root) or L._is_junction(root) or drive_root(root):
            raw=self.ask("Music folder (drop/type its path; 0 back): ")
            if raw=="0":return None
            root=os.path.abspath(os.path.expanduser(raw.strip('"')))
            if not os.path.isdir(root) or os.path.islink(root) or L._is_junction(root) or drive_root(root):
                self.say("Choose a music folder, rather than an entire drive or a link.")
                return None
            self.state["library"]=root
            self.catalog=None
            self.save()
        return root

    def scan(self,refresh=False):
        root=self.root()
        if root is None:return []
        if self.catalog is not None and not refresh:return self.catalog
        self.say("Reading song names and lyric status; no models or online lookups.")
        entries,_=L.walk(root,[str(self.home)])
        audio=[f for f in entries if Path(f[0]).suffix.lower() in L.AUDIO_EXT]
        def read(item):
            path,rel,size,_=item
            try:
                return L.read_song(path,rel,size,redo=True,retime_mode="guided")
            except Exception as e:
                s=L.Song(path=path,rel=rel,size=size)
                s.skip="unreadable ("+type(e).__name__+")"
                return s
        with ThreadPoolExecutor(4) as pool:
            self.catalog=sorted(pool.map(read,audio),key=lambda s:s.rel.casefold())
        L.group_same_name(self.catalog)
        return self.catalog

    def status(self,s):
        tier=s.previous_tier if s.previous_tier is not None else s.tier
        return s.skip or L.TIER_NAME[tier]

    def choose(self,songs):
        if not songs:
            self.say("No songs in this view.");return []
        shown=list(songs);page=0
        while True:
            start=page*25
            self.say("\nSongs %d–%d of %d" % (start+1,min(start+25,len(shown)),len(shown)))
            for i,s in enumerate(shown[start:start+25],start+1):
                self.say("%4d  %s  [%s]" % (i,s.rel,self.status(s)))
            self.say("1 Select numbers   2 Next page   3 Previous page   4 Search   5 Select this entire view   0 Back")
            pick=self.ask("> ")
            if pick=="0":return []
            if pick=="1":
                try:return [shown[i] for i in selection(self.ask("Song numbers (1,3-5; 0 back): "),len(shown))]
                except ValueError as e:self.say(str(e))
            elif pick=="2":page=min(page+1,(len(shown)-1)//25)
            elif pick=="3":page=max(0,page-1)
            elif pick=="4":
                term=self.ask("Name contains (empty resets): ").casefold()
                shown=[s for s in songs if term in s.rel.casefold()] if term else list(songs)
                if not shown:self.say("No matches.");shown=list(songs)
                page=0
            elif pick=="5":return shown

    def history(self):
        root=self.root()
        result={}
        if root is None:return result
        for log in sorted(Path(library_home(str(self.home),root)).glob("run */log.jsonl")):
            try:
                with log.open(encoding="utf-8") as f:
                    for line in f:
                        r=json.loads(line)
                        if isinstance(r,dict) and isinstance(r.get("song"),str):result[r["song"]]=r
            except (OSError,ValueError,TypeError):continue
        return result

    def job(self,songs,redo=False,whole=False,lyrics_file=None,mode_hint=None):
        if lyrics_file:
            songs=[L.read_song(s.path,s.rel,s.size,redo=True,lyrics_file=lyrics_file) for s in songs]
        songs=[s for s in songs if not s.skip]
        if not songs:self.say("No eligible songs selected.");return
        mode="current"
        if redo and mode_hint is not None:
            mode=mode_hint
        elif redo:
            self.say("1 Keep current words, discard old times\n2 Keep current words and line guides\n3 Fetch fresh lyrics\n0 Cancel")
            pick=self.ask("> ")
            if pick not in ("1","2","3"):return
            mode={"1":"current","2":"guided","3":"fresh"}[pick]
        self.say("\nSelected: %d songs; %s of audio. Speed: %s." % (len(songs),duration(sum(s.seconds for s in songs)),self.state["speed"]))
        self.say("Only these songs and their sidecars are backed up. Audio/tags are never changed.")
        if redo:self.say("Existing .elrc timing will be replaced only after a verified backup and successful checks. Failed results keep the original.")
        self.say("1 Start   0 Cancel")
        if self.ask("> ")!="1":return
        if whole:
            self.say("This reruns the entire eligible library, including existing Enhanced LRC. It can take hours.")
            if self.ask("Are you completely sure? 1 Yes, redo library   0 Cancel: ")!="1":return
        groups={}
        for s in songs:
            groups.setdefault(os.path.splitdrive(os.path.abspath(s.path))[0],[]).append(s.path)
        for paths in groups.values():
            root=self.state["library"]
            if not root or not all(backup.inside(p,root) for p in paths):
                root=os.path.commonpath([os.path.dirname(p) for p in paths])
            # A list file avoids Windows command-length limits, and keeps report/restore
            # history under the chosen library even when selecting one nested album.
            chosen=self.home/"selections"/(uuid.uuid4().hex+".txt")
            chosen.parent.mkdir(parents=True,exist_ok=True)
            files.create_new(chosen,("\n".join(paths)+"\n").encode("utf-8"))
            args=[root,"--songs-from",str(chosen),"--yes","--speed",self.state["speed"],"--no-open"]
            if self.state["backup"]:args += ["--backup-to",self.state["backup"]]
            if self.state["offline"]:args.append("--offline")
            if redo:args += ["--redo","--retime-mode",mode,"--confirm-redo"]
            else:args.append("--retry")
            if lyrics_file:args += ["--lyrics-file",lyrics_file]
            code=self.execute(args)
            self.say("Run finished (status %s). Failures were recorded without waiting for input." % code)
            if code==130:break
        self.catalog=None

    def dropped(self):
        raw=self.ask("Drop selected audio files here (0 back): ")
        if raw=="0":return []
        try:
            paths=dropped_files(raw)
            return [L.read_song(p,os.path.basename(p),os.path.getsize(p),redo=True,retime_mode="guided") for p in paths]
        except (OSError,ValueError) as e:
            self.say(str(e));return []

    def retime_selected(self):
        self.say("1 Select by song numbers   2 Drop audio files   3 Use files passed to this window   0 Back")
        pick=self.ask("> ")
        if pick=="1":songs=self.choose(self.scan())
        elif pick=="2":songs=self.dropped()
        elif pick=="3":
            songs=[L.read_song(p,os.path.basename(p),os.path.getsize(p),redo=True,retime_mode="guided") for p in self.initial]
        else:return
        self.job(songs,redo=True)

    def source_choice(self):
        history=self.history()
        all_songs=self.scan()
        failed=[s for s in all_songs if history.get(s.rel,{}).get("status") in FAILURES or
                (s.previous_tier if s.previous_tier is not None else s.tier) in (L.NONE,L.PLAIN)]
        self.say("1 Missing/failed songs   2 Any song   0 Back")
        pick=self.ask("> ")
        if pick not in ("1","2"):return
        chosen=self.choose(failed if pick=="1" else all_songs)
        if len(chosen)!=1:self.say("Choose one song for a lyric source.");return
        s=chosen[0]
        self.say("1 Paste a Genius page   2 Choose a lyric text file   3 Open Genius search   4 Use automatic search again   0 Back")
        action=self.ask("> ")
        if action=="3":
            webbrowser.open("https://genius.com/search?"+urllib.parse.urlencode({"q":s.artist+" "+s.title}));return
        if action=="2":
            path=self.ask("Lyric text file (0 back): ").strip('"')
            if path=="0":return
            if not os.path.isfile(path) or os.path.islink(path):self.say("Choose an existing text file.");return
            self.job([s],redo=True,lyrics_file=os.path.abspath(path),mode_hint="current");return
        if action not in ("1","4"):return
        url="auto" if action=="4" else genius_song_url(self.ask("Genius song URL (0 back): "))
        if not url:self.say("That is not a public Genius song page.");return
        target=Path(s.base+".lyrics-source.txt")
        data=(url+"\r\n").encode("utf-8")
        try:
            if os.path.lexists(target):
                if target.is_symlink() or target.stat().st_size>4096:raise OSError("This source file needs manual inspection.")
                old=files.sha256(target)
                saved=self.home/"source-choice backups"/uuid.uuid4().hex/target.name
                saved.parent.mkdir(parents=True)
                shutil.copy2(target,saved)
                files.replace_backed_up(target,data,saved,old)
            else:files.create_new(target,data)
            self.say("Source choice saved. It will be used on the next run; automatic batches never wait for this step.")
            self.catalog=None
        except OSError as e:self.say(str(e));return
        if self.ask("1 Rerun this song now   0 Back: ")=="1":
            fresh=L.read_song(s.path,s.rel,s.size,redo=True,retime_mode="guided")
            self.job([fresh],redo=True,mode_hint="fresh")

    def reports(self):
        root=self.root()
        if root is None:return
        self.say("1 Open a report   2 Restore previous timing   3 Undo new additions   0 Back")
        pick=self.ask("> ")
        if pick=="1":
            paths=sorted(Path(library_home(str(self.home),root)).glob("run */report.html"),reverse=True)
            for i,p in enumerate(paths[:30],1):self.say("%d  %s" % (i,p.parent.name))
            try:
                indices=selection(self.ask("Report number (0 back): "),min(30,len(paths)))
                if len(indices)==1:webbrowser.open(paths[indices[0]].resolve().as_uri())
            except ValueError as e:self.say(str(e))
        elif pick=="2":
            rows=rerun.records(str(self.home),root)
            for i,r in enumerate(rows,1):self.say("%d  %s" % (i,r["rel"]))
            try:
                chosen=selection(self.ask("Restore song numbers (0 back): "),len(rows))
                if chosen and self.ask("Restore these previous timings? 1 Yes   0 Cancel: ")=="1":
                    for i in chosen:
                        try:rerun.restore(str(self.home),root,rows[i]);self.say("Restored: "+rows[i]["rel"])
                        except OSError as e:self.say(str(e))
            except ValueError as e:self.say(str(e))
            self.catalog=None
        elif pick=="3":
            self.say("This archives unmodified additions made by WordLyrics. Retimed originals use Restore previous timing.")
            if self.ask("1 Continue   0 Cancel: ")=="1":self.execute(["undo",root,"--yes"])
            self.catalog=None

    def settings(self):
        self.say("1 Change library   2 Backup folder   3 Speed   4 Online/offline   5 Provider status   0 Back")
        pick=self.ask("> ")
        if pick=="1":self.state["library"]="";self.root()
        elif pick=="2":
            raw=self.ask("Backup folder (empty = automatic; 0 back): ")
            if raw!="0":self.state["backup"]=os.path.abspath(os.path.expanduser(raw.strip('"'))) if raw else ""
        elif pick=="3":
            choice=self.ask("1 Full   2 Half   3 Light   0 Back: ")
            if choice in ("1","2","3"):self.state["speed"]={"1":"full","2":"half","3":"light"}[choice]
        elif pick=="4":
            choice=self.ask("1 Online lookup   2 Offline (existing words only)   0 Back: ")
            if choice in ("1","2"):self.state["offline"]=choice=="2"
        elif pick=="5":self.execute(["providers"])
        self.save()

    def run(self):
        while True:
            self.say("\nWordLyrics %s — %s" % (__version__,self.state["library"] or "choose a music folder"))
            self.say("1 Generate missing word timing\n2 Browse lyric status\n3 Retry missing/failed songs\n4 Rerun selected songs\n5 Choose lyrics for a song (optional)\n6 Redo the whole library\n7 Reports, backups and restore\n8 Settings\n9 Check installation / setup\n0 Exit")
            pick=self.ask("> ")
            if pick=="0":return 0
            try:
                if pick=="1":
                    songs=[s for s in self.scan() if (s.previous_tier if s.previous_tier is not None else s.tier)!=L.WORD]
                    self.job(songs)
                elif pick=="2":
                    songs=self.scan()
                    choice=self.ask("1 All   2 No lyrics   3 Plain/line lyrics   4 Word timing   0 Back: ")
                    if choice=="0":continue
                    tiers={"2":{L.NONE,L.INSTRUMENTAL},"3":{L.PLAIN,L.LINE},"4":{L.WORD}}
                    shown=[s for s in songs if choice=="1" or (s.previous_tier if s.previous_tier is not None else s.tier) in tiers.get(choice,set())]
                    selected=self.choose(shown)
                    if selected:self.job(selected,redo=True)
                elif pick=="3":
                    history=self.history()
                    songs=[s for s in self.scan() if history.get(s.rel,{}).get("status") in FAILURES or
                           (s.previous_tier if s.previous_tier is not None else s.tier) in (L.NONE,L.PLAIN)]
                    selected=self.choose(songs)
                    if selected:self.job(selected,redo=True,mode_hint="fresh")
                elif pick=="4":self.retime_selected()
                elif pick=="5":self.source_choice()
                elif pick=="6":self.job(self.scan(),redo=True,whole=True)
                elif pick=="7":self.reports()
                elif pick=="8":self.settings()
                elif pick=="9":
                    action=self.ask("1 Check installation   2 Setup models   0 Back: ")
                    if action in ("1","2"):self.execute(["check" if action=="1" else "setup"])
            except (OSError,ValueError) as e:
                self.say("Nothing unsafe was forced: "+str(e))


def run_menu(home,initial,executor,reader=None,writer=None,options=None):
    return Menu(home,initial,executor,reader,writer,options).run()
