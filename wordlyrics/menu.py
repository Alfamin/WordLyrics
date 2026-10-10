"""Numbered terminal controls. Batch work never asks for help with individual failures."""
from concurrent.futures import ThreadPoolExecutor,as_completed
import json
import os
import re
from pathlib import Path
import shlex
import shutil
import urllib.parse
import uuid
import webbrowser

from . import __version__,backup,files,folders,library as L,rerun,resume,search
from .pipeline import library_home
from .sources import genius_song_url
from .ui import duration, _enable_ansi

DIGITS=str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩،","01234567890123456789,")
FAILURES={"no_lyrics","network_error","not_timed","rejected","failed","not_reached","lines_only"}


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
        self.selected_roots=[]
        self.origin={}
        self.folder_notes=[]
        self.scan_unreadable={}
        self.last_job_code=0
        self.last_notice=""
        self.color=writer is None and _enable_ansi() and not os.environ.get("NO_COLOR")
        try:
            data=json.loads(self.preferences.read_text(encoding="utf-8"))
            for key in self.state:
                if isinstance(data.get(key),type(self.state[key])):
                    self.state[key]=data[key]
            self.selected_roots=[p for p in data.get("libraries",[]) if isinstance(p,str) and not folders.usable(p)[1]]
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
            self.selected_roots=[self.state["library"]]
        elif initial and all(os.path.isdir(p) for p in initial):
            self.selected_roots=folders.unique(initial)
            self.state["library"]=self.selected_roots[0]
        elif initial:
            self.initial=[os.path.abspath(p) for p in initial if os.path.isfile(p)]
            if self.initial:
                self.state["library"]=os.path.dirname(self.initial[0])
                self.selected_roots=[self.state["library"]]
        elif not self.selected_roots:
            found,self.folder_notes=folders.discover()
            if len(found)==1:
                self.selected_roots=found
                self.state["library"]=found[0]
            elif len(found)>1:
                self.state["library"]=""
            elif self.state["library"] and not folders.usable(self.state["library"])[1]:
                self.selected_roots=[self.state["library"]]
        elif not initial:
            found,notes=folders.discover()
            self.folder_notes.extend(notes)
            more=[p for p in found if not any(backup.inside(p,selected) for selected in self.selected_roots)]
            if more:self.folder_notes.append("Noctis has %d other folder%s. Choose 4 to include them." % (len(more),"" if len(more)==1 else "s"))

    def panel(self,title,body="",fresh=False):
        if fresh and self.color:self.say("\033[2J\033[H")
        line="─"*min(68,max(36,shutil.get_terminal_size((80,24)).columns-4))
        heading="\n%s\n  %s\n%s" % (line,title,line)
        self.say(("\033[1;36m"+heading+"\033[0m") if self.color else heading)
        if body:self.say(body)

    def ask(self,prompt,numbers=True):
        try:
            label="\n  WORDLYRICS INPUT  |  " + (prompt if prompt!="> " else "Type an option number, then Enter")
            self.say(("\033[1;33m"+label+"\033[0m") if self.color else label)
            value=self.read("  WordLyrics > ").strip()
            return value.translate(DIGITS) if numbers or value in ("۰","٠") else value
        except (EOFError,KeyboardInterrupt):
            return "0"

    def save(self):
        self.home.mkdir(parents=True,exist_ok=True)
        temp=self.home/(".menu-settings-"+uuid.uuid4().hex+".tmp")
        with temp.open("x",encoding="utf-8") as f:
            json.dump(dict(self.state,libraries=self.selected_roots),f)
        os.replace(temp,self.preferences)

    def choose_folders(self):
        found,notes=folders.discover()
        self.panel("CHOOSE MUSIC FOLDERS")
        for note in notes:self.say(note)
        if found:
            self.say("  [1] All %d Noctis folders" % len(found))
            for i,p in enumerate(found,2):self.say("  [%d] %s" % (i,p))
            custom=len(found)+2
            self.say("  [%d] Choose another folder\n  [0] Back" % custom)
            raw=self.ask("Choose folders (one number, or numbers such as 2,3): ")
            if raw=="0":return []
            if raw=="1":chosen=found
            elif raw==str(custom):chosen=[]
            else:
                try:
                    indices=selection(raw,len(found)+1)
                    if 0 in indices:raise ValueError("Use 1 alone for all folders.")
                    chosen=[found[i-1] for i in indices]
                except (ValueError,IndexError):self.say("Choose the displayed folder numbers.");return []
                if not chosen:return []
        else:chosen=[]
        if not chosen:
            raw=self.ask("Drop/type a music folder path (0 back): ",numbers=False).strip('"')
            if raw=="0":return []
            path,error=folders.usable(raw)
            if error:self.say(error);return []
            chosen=[path]
        self.selected_roots=folders.unique(chosen)
        self.state["library"]=self.selected_roots[0]
        self.catalog=None
        self.origin={}
        self.save()
        return self.selected_roots

    def roots(self):
        if not self.selected_roots:
            if self.state["library"] and not folders.usable(self.state["library"])[1]:
                self.selected_roots=[self.state["library"]]
            else:return self.choose_folders()
        good=[]
        for p in self.selected_roots:
            root,error=folders.usable(p)
            if error:self.say("%s: %s" % (root,error))
            else:good.append(root)
        if not good:
            self.selected_roots=[]
            return self.choose_folders()
        return folders.unique(good)

    def root(self):
        roots=self.roots()
        if not roots:return None
        if len(roots)==1:return roots[0]
        self.panel("CHOOSE A FOLDER FOR REPORTS / RESTORE")
        for i,p in enumerate(roots,1):self.say("  [%d] %s" % (i,p))
        try:
            selected=selection(self.ask("Folder number (0 back): "),len(roots))
            return roots[selected[0]] if len(selected)==1 else None
        except ValueError as e:self.say(str(e));return None

    def scan(self,refresh=False):
        roots=self.roots()
        if not roots:return []
        if self.catalog is not None and not refresh:return self.catalog
        self.panel("SCANNING YOUR MUSIC", "Reading song names and lyric status. No AI or online searches yet.")
        audio=[]
        self.origin={}
        self.scan_unreadable={}
        for root in roots:
            unreadable=[]
            entries,links=L.walk(root,[str(self.home)],unreadable)
            found=[f for f in entries if Path(f[0]).suffix.lower() in L.AUDIO_EXT]
            audio.extend(found)
            for entry in found:self.origin[entry[0]]=root
            self.say("  %d songs  |  %s" % (len(found),root))
            if unreadable:
                self.say("  Cannot read: "+", ".join(unreadable[:5]))
                self.scan_unreadable[root]=unreadable
            if links:self.say("  %d linked files/folders were left alone." % len(links))
        def read(item):
            path,rel,size,_=item
            try:
                return L.read_song(path,rel,size,redo=True,retime_mode="guided")
            except Exception as e:
                s=L.Song(path=path,rel=rel,size=size)
                from .recovery import permission_error
                if permission_error(e):s.result["preflight_error"]="PERMISSION_DENIED: access is needed before this song can be checked"
                else:s.skip="unreadable ("+type(e).__name__+")"
                return s
        with ThreadPoolExecutor(4) as pool:
            results=[]
            pending=[pool.submit(read,item) for item in audio]
            for n,future in enumerate(as_completed(pending),1):
                results.append(future.result())
                if n%50==0 or n==len(audio):self.say("  Reading names/status: %d / %d" % (n,len(audio)))
            self.catalog=sorted(results,key=lambda s:s.rel.casefold())
        L.group_same_name(self.catalog)
        self.say("\nFound %d songs across %d folder%s." % (len(self.catalog),len(roots),"" if len(roots)==1 else "s"))
        return self.catalog

    def status(self,s):
        tier=s.previous_tier if s.previous_tier is not None else s.tier
        return s.skip or s.result.get("preflight_error") or L.TIER_NAME[tier]

    def choose(self,songs):
        if not songs:
            self.say("No songs in this view.");return []
        shown=list(songs);page=0
        while True:
            start=page*25
            self.say("\nSongs %d–%d of %d" % (start+1,min(start+25,len(shown)),len(shown)))
            for i,s in enumerate(shown[start:start+25],start+1):
                location=(Path(self.origin.get(s.path,"music")).name+" / ") if len(self.selected_roots)>1 else ""
                self.say("%4d  %s%s  [%s]" % (i,location,s.rel,self.status(s)))
            self.say("1 Select numbers   2 Next page   3 Previous page   4 Search   5 Select this entire view   0 Back")
            pick=self.ask("> ")
            if pick=="0":return []
            if pick=="1":
                try:return [shown[i] for i in selection(self.ask("Song numbers (1,3-5; 0 back): "),len(shown))]
                except ValueError as e:self.say(str(e))
            elif pick=="2":page=min(page+1,(len(shown)-1)//25)
            elif pick=="3":page=max(0,page-1)
            elif pick=="4":
                term=self.ask("Song, artist, album or filename (partial names work; empty resets): ",numbers=False)
                shown,approximate=search.find(songs,term)
                if approximate:self.say("No direct matches. Closest spelling matches are shown; check the artist before selecting.")
                if not shown:self.say("No matches.");shown=list(songs)
                page=0
            elif pick=="5":return shown

    def search_songs(self,songs=None,query=None):
        songs=self.scan() if songs is None else list(songs)
        if not songs:self.say("No songs available to search.");return []
        while True:
            query=query if query is not None else self.ask("Type part of a song name or artist (0 back): ",numbers=False)
            if not query or query=="0":return []
            found,approximate=search.find(songs,query);page=0
            if not found:self.say("No matches for "+query+". Try another part of the title or artist.");query=None;continue
            while True:
                self.panel("SEARCH RESULTS: "+query,"Closest spelling matches - check the artist." if approximate else "%d matching songs" % len(found))
                for i,s in enumerate(found[page*25:page*25+25],page*25+1):
                    self.say("  [%d] %s%s  [%s]\n      %s" % (i,(s.artist+" - ") if s.artist else "",s.title or s.name,self.status(s),s.path))
                self.say("\n  Type song numbers (such as 1 or 1,3). N Next page | P Previous | S New search | 0 Back")
                raw=self.ask("Song number(s), N/P/S, or another search: ",numbers=False)
                action=raw.casefold()
                if action=="0":return []
                if action=="n":page=min(page+1,(len(found)-1)//25);continue
                if action=="p":page=max(page-1,0);continue
                if action=="s":query=None;break
                try:
                    indices=selection(raw,len(found))
                    if indices:return [found[i] for i in indices]
                except ValueError:
                    if any(c.isalpha() for c in raw):query=raw;break
                    self.say("Choose a number shown in this result list.")

    def song_actions(self,songs):
        while songs:
            self.panel("REPAIR SONG", "\n".join("  "+((s.artist+" - ") if s.artist else "")+(s.title or s.name) for s in songs),fresh=True)
            if self.last_notice:self.say("\n  Last result: "+self.last_notice)
            self.say("\n  [1] Fetch fresh lyrics and redo\n  [2] Keep words and redo timing\n  [3] Choose a lyric source / file\n  [4] Show song details\n  [5] Edit or paste custom lyrics\n\n  [0] Back to main menu")
            action=self.ask("> ")
            if action=="0":return
            if action=="1":self.job(songs,redo=True,mode_hint="fresh",repair_lyrics=True)
            elif action=="2":self.job(songs,redo=True,mode_hint="current")
            elif action=="3":
                if len(songs)!=1:self.last_notice="Select one song to choose its source."
                else:self.source_choice(selected_song=songs[0])
            elif action=="4":
                self.panel("SONG DETAILS",fresh=True)
                for s in songs:self.say("%s\n  Artist: %s | Title: %s\n  Lyrics: %s" % (s.path,s.artist or "unknown",s.title or s.name,self.status(s)))
                self.ask("Enter or 0 to go back")
            elif action=="5":
                if len(songs)!=1:self.last_notice="Select one song to edit its lyrics."
                else:self.custom_lyrics(songs[0])
            else:self.last_notice="Choose a number shown in this menu."
            refreshed=[]
            for s in songs:
                if os.path.isfile(s.path):
                    try:s=L.read_song(s.path,s.rel,os.path.getsize(s.path),redo=True,repair_lyrics=True,ignore_source=True)
                    except OSError:pass
                refreshed.append(s)
            songs=refreshed

    def custom_lyrics(self,song):
        from . import drafts
        self.panel("EDIT / PASTE LYRICS", "Work on a private draft. Existing lyrics stay active until timing succeeds.",fresh=True)
        self.say("  [1] Open current words in a text editor\n  [2] Paste the full lyrics here\n  [3] Use an existing lyric text file\n  [0] Back")
        action=self.ask("> ")
        if action=="1":
            path=drafts.create(str(self.home),song)
            self.say("Draft: "+path+"\nEdit or replace its words, then SAVE the file. If it is blank, paste the full lyrics.")
            try:drafts.open_editor(path)
            except OSError as e:self.say(str(e))
            if self.ask("1 Use the saved draft and review timing   0 Keep draft for later: ")!="1":return
        elif action=="2":
            self.panel("PASTE LYRICS", "Paste plain text or synced LRC. Blank lines are ignored.",fresh=True)
            finish="When finished: type .done then Enter. Type .cancel to go back."
            self.say(("\033[1;33m"+finish+"\033[0m") if self.color else finish)
            lines=[];length=0
            while True:
                try:line=self.read("  Line %d | .done to finish > " % (len(lines)+1))
                except EOFError:
                    if lines:break
                    return
                except KeyboardInterrupt:return
                if line.strip()==".cancel":return
                if line.strip()==".done":break
                if not line.strip():continue
                length+=len(line)
                if length>500_000:self.say("Lyrics are too large; use a normal song text file.");return
                lines.append(line)
            path=drafts.create(str(self.home),song,"\n".join(lines))
        elif action=="3":
            path=self.ask("Lyric text file (0 back): ",numbers=False).strip('"')
            if path=="0":return
        else:return
        if not os.path.isfile(path) or os.path.islink(path):self.say("Choose an existing, unlinked text file.");return
        if not L._read_text(path).strip():self.say("The draft is empty. Paste the full lyrics, save it, then choose it as a lyric text file.");return
        from . import guides
        pasted=L._read_text(path);timed,why=guides.rows(pasted,song.seconds)
        self.panel("LYRICS CAPTURED", "%d lyric lines | %d usable line timestamps" % (len(guides.words(pasted).splitlines()),len(timed)),fresh=True)
        if timed:self.say("Supplied line guides will be used; word timing is recalculated.")
        else:
            preview=L.read_song(song.path,song.rel,os.path.getsize(song.path),redo=True,lyrics_file=path,repair_lyrics=True,ignore_source=True)
            if preview.tier==L.LINE:
                self.say("Using %d existing compatible line guides for these words; word timing is recalculated." % len(guides.rows(preview.text,preview.seconds)[0]))
            else:self.say("No compatible line guides: "+why+". Plain alignment may need synced lyrics for this recording.")
        self.job([song],redo=True,lyrics_file=os.path.abspath(path),mode_hint="current",repair_lyrics=True,automatic=False)

    def lrclib_choice(self,song):
        from . import drafts
        query=self.ask("LRCLIB song / artist search (Enter uses this song; 0 back): ",numbers=False)
        if query=="0":return
        self.panel("SEARCHING LRCLIB", "Matching this recording. Valid line guides are retained; word timing is recalculated.",fresh=True)
        try:rows=drafts.lrclib_results(str(self.home),song,query)
        except OSError as e:self.last_notice="LRCLIB search failed: "+str(e);return
        if not rows:self.last_notice="No usable uncensored LRCLIB results. Try Genius or custom lyrics.";return
        best=[row for row in rows if row["recording_match"] or row["near_match"]]
        alternate=False;page=0
        page_size=max(2,min(5,(shutil.get_terminal_size((80,24)).lines-12)//4))
        label=lambda value:re.sub(r"[\x00-\x1f\x7f]"," ",str(value))[:100]
        while True:
            visible=rows if alternate else best
            pages=max(1,(len(visible)+page_size-1)//page_size);page=min(page,pages-1)
            start=page*page_size
            self.panel("LRCLIB RESULTS", "Your file: %s (%.1f seconds)" % (duration(song.seconds),song.seconds),fresh=True)
            if not visible:self.say("No close recording matches. Other versions may have a very different length.")
            else:self.say("%d results | page %d / %d" % (len(visible),page+1,pages))
            for i,row in enumerate(visible[start:start+page_size],start+1):
                offset=row["seconds_off"]
                difference="difference unknown" if offset is None else "difference %.1fs" % offset
                kind="synced lines" if row["synced"] else "plain words"
                self.say("\n  [%d] %s - %s\n      %s | %s | %s\n      Album: %s%s" % (i,label(row.get("artistName","")),label(row.get("trackName","")),str(row.get("duration","?"))+"s",difference,kind,label(row.get("albumName","")),(" | CHECK: "+row["warning"]) if row["warning"] else ""))
            self.say("\n  [N/P] Next / previous page\n  [A] %s\n  [0] Back" % ("Show closest matches" if alternate else "Show other versions / results"))
            reply=self.ask("Choose one result, A, or 0")
            if reply=="0":return
            if reply.lower()=="a":alternate=not alternate;page=0;continue
            if reply.lower()=="n":page=min(page+1,pages-1);continue
            if reply.lower()=="p":page=max(0,page-1);continue
            try:chosen=selection(reply,len(visible))
            except ValueError:continue
            if len(chosen)!=1:continue
            row=visible[chosen[0]];break
        path=drafts.create(str(self.home),song,row["chosen_text"],label="LRCLIB")
        self.say("Selected LRCLIB words saved as a private draft: "+path)
        self.job([song],redo=True,lyrics_file=path,mode_hint="current",repair_lyrics=True,automatic=False)

    def repair_path(self,path,lyrics_file=None):
        path=os.path.abspath(path)
        if not os.path.isfile(path) or os.path.islink(path) or Path(path).suffix.lower() not in L.AUDIO_EXT:
            self.panel("SONG UNAVAILABLE", "Choose an existing audio file, rather than a folder or link.");return 2
        known=self.selected_roots+folders.discover()[0]
        matching=[root for root in known if backup.inside(path,root)]
        root=max(matching,key=len) if matching else os.path.dirname(path)
        self.selected_roots=[root];self.state["library"]=root;self.origin[path]=root
        try:
            song=L.read_song(path,os.path.relpath(path,root),os.path.getsize(path),redo=True,repair_lyrics=True,ignore_source=True)
            self.panel("WRONG LYRICS / REPAIR THIS SONG", ((song.artist+" - ") if song.artist else "")+(song.title or song.name)+"\n"+path)
            if lyrics_file:self.job([song],redo=True,lyrics_file=lyrics_file,mode_hint="current",repair_lyrics=True,automatic=False)
            else:self.song_actions([song])
            return self.last_job_code
        except (OSError,ValueError) as e:self.last_notice=str(e);self.panel("SONG NEEDS ATTENTION",str(e));return 1

    def history(self):
        result={}
        for root in self.roots():
            for log in sorted(Path(library_home(str(self.home),root)).glob("run */log.jsonl")):
                try:
                    with log.open(encoding="utf-8") as f:
                        for line in f:
                            r=json.loads(line)
                            if isinstance(r,dict) and isinstance(r.get("song"),str):result[os.path.join(root,r["song"])]=r
                except (OSError,ValueError,TypeError):continue
        return result

    def prior(self,history,song):
        return history.get(song.path,history.get(song.rel,{})).get("status")

    def job(self,songs,redo=False,whole=False,lyrics_file=None,mode_hint=None,repair_lyrics=False,automatic=True):
        self.last_job_code=0
        if lyrics_file or repair_lyrics:
            songs=[L.read_song(s.path,s.rel,os.path.getsize(s.path),redo=True,lyrics_file=lyrics_file,
                               retime_mode="fresh" if repair_lyrics else "current",repair_lyrics=repair_lyrics,
                               ignore_source=repair_lyrics and automatic) for s in songs]
        for song in songs:
            if song.skip:self.say("%s: %s" % (song.rel,song.skip))
        songs=[s for s in songs if not s.skip]
        if not songs:self.panel("NOTHING TO START", "No eligible songs selected. Use Songs to see skip reasons, or Advanced > Rerun to replace protected .elrc timing.");return
        repair_paths=set()
        if not redo:
            damaged=[s for s in songs if s.retime_path and s.previous_tier!=L.WORD]
            if damaged:
                self.panel("EXISTING FILES NEED WORD TIMING", "%d .elrc files contain plain/line lyrics rather than word timestamps. Repair requires replacing those files after a verified backup." % len(damaged))
                self.say("  [1] Repair these files and generate other missing timestamps\n  [2] Leave these files alone; generate the others\n  [0] Cancel")
                choice=self.ask("> ")
                if choice=="1":repair_paths={s.path for s in damaged}
                elif choice=="2":songs=[s for s in songs if s not in damaged]
                else:return
                if not songs:self.say("No other songs need processing. Existing files were left alone.");return
        mode="current"
        if redo and mode_hint is not None:
            mode=mode_hint
        elif redo:
            self.say("1 Keep current words and valid line guides (recommended)\n2 Keep words, locate everything from scratch (advanced)\n3 Fetch fresh lyrics\n0 Cancel")
            pick=self.ask("> ")
            if pick not in ("1","2","3"):return
            mode={"1":"current","2":"unanchored","3":"fresh"}[pick]
        self.panel("READY TO %s" % ("RERUN" if redo else "GENERATE TIMESTAMPS"), "Selected: %d songs; %s of audio. Speed: %s." % (len(songs),duration(sum(s.seconds for s in songs)),self.state["speed"]))
        self.say("Only these songs and their sidecars are backed up. Audio/tags are never changed.")
        if redo:self.say("Existing .elrc timing will be replaced only after a verified backup and successful checks. Failed results keep the original.")
        if repair_lyrics:
            self.say(("Your chosen words will be timed again. Valid line timestamps are rough guides; old word timestamps are recalculated. No extra lyric lookup is needed." if lyrics_file else "Fresh online lyrics will be checked against the recording. Old words are ignored.")+" Preferred .ttml/.lyricsfile files will be archived only when accepted word timing is ready; Restore can bring them back.")
            if automatic:self.say("The old saved Genius choice is ignored for this repair.")
        if redo:
            self.say("\n  [1] START RERUN\n  [0] CANCEL - leave everything as it is\n")
            if self.ask("> ")!="1":return
        else:self.say("Starting now. Ctrl+C stops safely; completed songs are kept.")
        if whole:
            self.say("This reruns the entire eligible library, including existing Enhanced LRC. It can take hours.")
            if self.ask("Are you completely sure? 1 Yes, redo library   0 Cancel: ")!="1":return
        groups={}
        for s in songs:
            root=self.origin.get(s.path) or self.state["library"]
            if not root or not backup.inside(s.path,root):root=os.path.dirname(s.path)
            groups.setdefault((root,s.path in repair_paths),[]).append(s.path)
        total_timed=total_attention=0
        for (root,repair),paths in groups.items():
            if not root or not all(backup.inside(p,root) for p in paths):
                root=os.path.commonpath([os.path.dirname(p) for p in paths])
            # A list file avoids Windows command-length limits, and keeps report/restore
            # history under the chosen library even when selecting one nested album.
            chosen=self.home/"selections"/(uuid.uuid4().hex+".txt")
            chosen.parent.mkdir(parents=True,exist_ok=True)
            files.create_new(chosen,("\n".join(paths)+"\n").encode("utf-8"))
            args=[root,"--songs-from",str(chosen),"--yes","--speed",self.state["speed"],"--no-open"]
            result=chosen.with_suffix(".result.json")
            args += ["--result",str(result)]
            if self.state["backup"]:args += ["--backup-to",self.state["backup"]]
            if lyrics_file or (self.state["offline"] and not repair_lyrics):args.append("--offline")
            if redo or repair:args += ["--redo","--retime-mode",mode,"--confirm-redo"]
            else:args.append("--retry")
            if lyrics_file:args += ["--lyrics-file",lyrics_file]
            if repair_lyrics:args.append("--repair-lyrics")
            if repair_lyrics and automatic:args.append("--ignore-lyric-source")
            code=self.execute(args)
            self.last_job_code=code or self.last_job_code
            try:
                data=json.loads(result.read_text(encoding="utf-8"))
                summary=data["summary"]
                self.panel("RESULT", summary["message"])
                self.last_notice=summary["message"]
                first_problem=next((item.get("reason") for item in data.get("songs",[]) if item.get("reason") and item.get("status") in FAILURES),"")
                if first_problem:self.last_notice += "\n    Reason: "+first_problem.split(". ")[0]
                total_timed += summary["timed"]
                total_attention += summary["needs_attention"]
                if data.get("error"):self.say(data["error"])
                if data.get("exit_code"):
                    self.say("Your progress is saved. Choose 7 Resume / fix unfinished jobs to fix a problem and retry only affected songs.")
                report_path=Path(data.get("run_folder", ""))/"report.html"
                self.say("Report: "+str(report_path))
                if repair_lyrics and summary["timed"]:self.say("In Noctis, select another song and return to this song to reload its new lyrics.")
            except (OSError,ValueError,KeyError,TypeError):
                self.last_notice="Run returned status %s without a result summary. Success has not been confirmed; read its report." % code
                self.panel("RESULT", "The run returned status %s. No result summary was available; read the messages above. Success has not been confirmed." % code)
                if code:total_attention += len(paths)
            if code==130:break
        if len(groups)>1:self.panel("ALL SELECTED FOLDERS", "%d songs received word timestamps; %d need attention." % (total_timed,total_attention))
        self.catalog=None

    def dropped(self):
        raw=self.ask("Drop selected audio files here (0 back): ",numbers=False)
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

    def source_choice(self,eligible=None,selected_song=None):
        history=self.history()
        all_songs=[selected_song] if selected_song is not None else self.scan() if eligible is None else eligible
        failed=[s for s in all_songs if self.prior(history,s) in FAILURES or
                (s.previous_tier if s.previous_tier is not None else s.tier) in (L.NONE,L.PLAIN)]
        if selected_song is not None:chosen=[selected_song]
        else:
            self.say("1 Missing/failed songs   2 Any song   0 Back")
            pick=self.ask("> ")
            if pick not in ("1","2"):return
            chosen=self.choose(failed if pick=="1" else all_songs)
        if len(chosen)!=1:self.say("Choose one song for a lyric source.");return
        s=chosen[0]
        if eligible is not None:
            s=L.read_song(s.path,s.rel,os.path.getsize(s.path),redo=True,retime_mode="guided")
        self.say("1 Paste a Genius page   2 Choose a lyric text file   3 Open Genius search   4 Use automatic search again   5 Search LRCLIB / choose a result   0 Back")
        action=self.ask("> ")
        if action=="5":self.lrclib_choice(s);return
        if action=="3":
            webbrowser.open("https://genius.com/search?"+urllib.parse.urlencode({"q":s.artist+" "+s.title}));return
        if action=="2":
            path=self.ask("Lyric text file (0 back): ",numbers=False).strip('"')
            if path=="0":return
            if not os.path.isfile(path) or os.path.islink(path):self.say("Choose an existing text file.");return
            self.job([s],redo=True,lyrics_file=os.path.abspath(path),mode_hint="current",repair_lyrics=selected_song is not None,automatic=False);return
        if action not in ("1","4"):return
        url="auto" if action=="4" else genius_song_url(self.ask("Genius song URL (0 back): ",numbers=False))
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
            self.job([fresh],redo=True,mode_hint="fresh",repair_lyrics=selected_song is not None,automatic=False)

    def reports(self):
        root=self.root()
        if root is None:return
        self.say("1 Open a report   2 Restore previous timing   3 Undo new additions   4 Restore archived incorrect lyric files   0 Back")
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
        elif pick=="4":
            from . import repair
            rows=repair.records(str(self.home),root)
            for i,row in enumerate(rows,1):self.say("%d  %s" % (i,row["rel"]))
            if not rows:self.say("No archived preferred lyric files to restore.");return
            chosen=selection(self.ask("File numbers to restore (0 back): "),len(rows))
            if chosen and self.ask("Restore these old lyrics? 1 Yes   0 Cancel: ")=="1":
                for i in chosen:
                    repair.restore(root,rows[i]);self.say("Restored: "+rows[i]["rel"])
            self.catalog=None
        elif pick=="3":
            self.say("This archives unmodified additions made by WordLyrics. Retimed originals use Restore previous timing.")
            if self.ask("1 Continue   0 Cancel: ")=="1":self.execute(["undo",root,"--yes"])
            self.catalog=None

    def settings(self):
        self.say("1 Change library   2 Backup folder   3 Speed   4 Online/offline   5 Provider status   0 Back")
        pick=self.ask("> ")
        if pick=="1":self.choose_folders()
        elif pick=="2":
            raw=self.ask("Backup folder (empty = automatic; 0 back): ",numbers=False)
            if raw!="0":self.state["backup"]=os.path.abspath(os.path.expanduser(raw.strip('"'))) if raw else ""
        elif pick=="3":
            choice=self.ask("1 Full   2 Half   3 Light   0 Back: ")
            if choice in ("1","2","3"):self.state["speed"]={"1":"full","2":"half","3":"light"}[choice]
        elif pick=="4":
            choice=self.ask("1 Online lookup   2 Offline (existing words only)   0 Back: ")
            if choice in ("1","2"):self.state["offline"]=choice=="2"
        elif pick=="5":self.execute(["providers"])
        self.save()

    def advanced(self):
        self.panel("ADVANCED", "  [1] Rerun selected songs\n  [2] Choose a lyric source\n  [3] Redo all selected folders\n  [4] Reports / backups / restore\n  [5] Speed / offline / backup settings\n  [6] Check installation\n  [7] Download / set up AI models\n  [8] Connection & setup diagnostics\n  [0] Back")
        pick=self.ask("> ")
        if pick=="1":self.retime_selected()
        elif pick=="2":self.source_choice()
        elif pick=="3":self.job(self.scan(),redo=True,whole=True)
        elif pick=="4":self.reports()
        elif pick=="5":self.settings()
        elif pick in ("6","7","8"):self.execute([{ "6":"check","7":"setup","8":"diagnose"}[pick]])

    def resume_job(self,run_dir):
        code=0
        while True:
            try:data,rows=resume.pending(str(self.home),run_dir)
            except (OSError,ValueError) as e:self.panel("SAVED JOB NEEDS ATTENTION",str(e));return 1
            if not rows:self.panel("THIS JOB IS COMPLETE", "No unfinished songs remain. Completed word timestamps were kept.");return 0
            self.selected_roots=[data["source"]];self.state["library"]=data["source"]
            folders_waiting=sum(bool(row.get("folder")) for row in rows)
            self.panel("NEEDS ACTION - YOUR PROGRESS IS SAVED", "%d unfinished songs; %d folders still need scanning. Completed songs will not be processed again." % (len(rows)-folders_waiting,folders_waiting))
            for row in rows[:8]:self.say("  %s: %s" % (Path(row["path"]).name,row["reason"]))
            if len(rows)>8:self.say("  ... %d more songs; see the full result report." % (len(rows)-8))
            self.say("\n  [1] Retry affected songs now\n  [2] Fix music-folder permissions (Windows administrator approval)\n  [3] Choose lyrics for an affected song\n  [4] Retry with fresh lyrics\n  [5] Choose another backup folder and continue\n  [6] Check connections & setup\n  [0] Return - keep this job saved\n")
            pick=self.ask("> ")
            if pick=="0":return code
            try:
                if pick in ("1","4"):
                    code=resume.submit(str(self.home),run_dir,self.execute,fresh=pick=="4")
                elif pick=="2":
                    from . import permissions
                    try:
                        outcome=json.loads((Path(run_dir)/"outcome.json").read_text(encoding="utf-8"))
                        targets=outcome.get("permission_targets") or [data["source"]]
                    except (OSError,ValueError):targets=[data["source"]]
                    targets=permissions.plan(targets,data["source"])
                    self.panel("PERMISSION FIX", "Windows will request administrator approval. Only the listed local targets are changed for your current Windows user. Audio bytes and other users' rules/ownership are preserved.")
                    for target in targets:self.say("  "+target)
                    if self.ask("1 Request administrator approval   0 Cancel: ")=="1":
                        repaired=permissions.repair(str(self.home),data["source"],targets,detailed=True)
                        self.say(repaired["message"])
                        if repaired["ok"]:code=resume.submit(str(self.home),run_dir,self.execute)
                elif pick=="3":
                    songs=[L.Song(row["path"],os.path.relpath(row["path"],data["source"])) for row in rows if not row.get("folder")]
                    if not songs:self.say("Fix folder access or reconnect the drive before choosing song lyrics.");continue
                    self.origin={s.path:data["source"] for s in songs}
                    self.source_choice(songs)
                elif pick=="5":
                    place=self.ask("New backup folder (0 cancel): ",numbers=False).strip('"')
                    if place and place!="0":code=resume.submit(str(self.home),run_dir,self.execute,backup_to=os.path.abspath(place))
                elif pick=="6":self.execute(["diagnose"])
            except (OSError,ValueError) as e:self.say(str(e)+" Your progress is still saved.")

    def unfinished(self):
        jobs=[]
        for request in sorted((self.home/"runs").glob("*/run */request.json"),key=lambda p:p.parent.name,reverse=True):
            try:
                _,rows=resume.pending(str(self.home),str(request.parent))
                if rows:jobs.append((request.parent,len(rows)))
            except (OSError,ValueError):continue
        if not jobs:self.panel("NO UNFINISHED SAVED JOBS");return
        self.panel("RESUME / FIX AN UNFINISHED JOB")
        for i,(path,count) in enumerate(jobs[:30],1):self.say("  [%d] %s | %s | %d unfinished items" % (i,path.parent.name,path.name,count))
        try:
            indices=selection(self.ask("Job number (0 back): "),min(len(jobs),30))
            if len(indices)==1:self.resume_job(str(jobs[indices[0]][0]))
        except ValueError as e:self.say(str(e))

    def run(self):
        while True:
            self.panel("WORDLYRICS %s" % __version__, "Word-by-word lyrics for your music",fresh=True)
            if self.last_notice:self.say("\n  Last result: "+self.last_notice)
            for p in self.selected_roots:self.say("  Music: "+p)
            if not self.selected_roots:self.say("  Music folders will be detected from Noctis when you start.")
            for note in self.folder_notes:self.say("  "+note)
            self.folder_notes=[]
            self.say("\n  [1] GENERATE MISSING TIMESTAMPS\n  [2] Songs & lyric status\n  [3] Retry songs that need attention\n  [4] Choose music folders\n  [5] Advanced / reports / restore\n  [6] Check connections & setup\n  [7] Resume / fix unfinished jobs\n  [8] SEARCH / FIX A SONG\n  [0] Exit\n")
            self.say("  You can also type part of a song name here to search directly.")
            raw=self.ask("> ",numbers=False)
            pick=raw.translate(DIGITS) if raw.translate(DIGITS).isdecimal() else raw
            if pick=="0":return 0
            try:
                if pick=="1":
                    songs=[s for s in self.scan() if (s.previous_tier if s.previous_tier is not None else s.tier)!=L.WORD]
                    self.job(songs)
                    if not songs and self.scan_unreadable:
                        for root in self.scan_unreadable:
                            self.panel("SAVING THE BLOCKED JOB", "This folder cannot be read. The saved job will let you request a permission fix and resume.")
                            self.execute([root,"--yes","--retry","--no-open"])
                        self.say("Choose 7 Resume / fix unfinished jobs to fix access and continue.")
                elif pick=="2":
                    songs=self.scan()
                    choice=self.ask("1 All   2 No lyrics   3 Plain/line lyrics   4 Word timing   0 Back: ")
                    if choice=="0":continue
                    tiers={"2":{L.NONE,L.INSTRUMENTAL},"3":{L.PLAIN,L.LINE},"4":{L.WORD}}
                    shown=[s for s in songs if choice=="1" or (s.previous_tier if s.previous_tier is not None else s.tier) in tiers.get(choice,set())]
                    selected=self.choose(shown)
                    if selected:self.song_actions(selected)
                elif pick=="3":
                    history=self.history()
                    songs=[s for s in self.scan() if self.prior(history,s) in FAILURES or
                           (s.previous_tier if s.previous_tier is not None else s.tier) in (L.NONE,L.PLAIN,L.LINE)]
                    selected=self.choose(songs)
                    if selected:self.job(selected,redo=True,mode_hint="fresh")
                elif pick=="4":self.choose_folders()
                elif pick=="5":self.advanced()
                elif pick=="6":self.execute(["diagnose"])
                elif pick=="7":self.unfinished()
                elif pick=="8":self.song_actions(self.search_songs())
                elif any(c.isalpha() for c in pick):self.song_actions(self.search_songs(query=pick))
            except (OSError,ValueError) as e:
                self.say("Nothing unsafe was forced: "+str(e))


def run_menu(home,initial,executor,reader=None,writer=None,options=None):
    return Menu(home,initial,executor,reader,writer,options).run()
