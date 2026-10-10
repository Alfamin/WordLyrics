"""One run over a music folder: backup -> read songs -> find lyrics -> listen and time the words -> check.

Safety rules that hold everywhere in this file
  * Songs and tags are only read. Normal runs create new sidecars exclusively.
  * Explicit retiming may replace an existing .elrc only after its independent backup
    verifies and the accepted output is saved as a candidate. No other existing file is replaced.
  * Every file written is listed, with its fingerprint, so it can be taken out again (see undo.py).
  * At the end the folder is compared with how it was before the run.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import __version__, audio, backup, decide, fetch, files as safe_files, models
from . import library as L
from . import separate as S
from . import timing as T
from .ui import DONE, Rate, Screen, duration, size


class Stop(Exception):
    pass


def library_home(home, source):
    """Folder where everything about one music folder is kept (reports, lists, the lyrics-service cache)."""
    name = os.path.basename(os.path.abspath(source).rstrip("\\/")) or "music"
    tag = hashlib.sha1(os.path.normcase(os.path.abspath(source)).encode("utf-8")).hexdigest()[:8]
    return os.path.join(home, "runs", "%s-%s" % ("".join(c if c.isalnum() or c in " -_" else "_" for c in name), tag))


def file_bytes(text):
    """Lyric files are written as UTF-8 with Windows line ends, no byte-order mark."""
    return (text.rstrip("\n").replace("\n", "\r\n") + "\r\n").encode("utf-8")


class Run:
    def __init__(self, source, opts, home):
        self.source = os.path.abspath(source)
        self.opts = opts
        self.home = home
        self.lib_home = library_home(home, self.source)
        self.started = datetime.now()
        self.run_dir = os.path.join(self.lib_home, "run " + self.started.strftime("%Y-%m-%d %H.%M.%S.%f"))
        self.stop = threading.Event()
        self.screen = Screen("WordLyrics - word-by-word lyrics for your music", plain=opts.plain_output)
        self.songs = []
        self.written = []                  # [{"rel", "sha256", "bytes", "kind"}]
        self.write_lock = threading.Lock()
        self.backup_rec = None
        self.engine = None
        self.engine_loader = None
        self.lyrics_client = None
        self.engine_error = None
        self.counts = {}
        self.check = None
        self.log = None
        self.fetch_done = threading.Event()
        self.problems = []
        self.interrupted = False
        self.exclude = [home]              # the tool's own folder, should it sit inside the music folder
        self.queued_n = 0                  # songs handed to the listener so far
        self.queued_audio = 0.0            # ... and their length in seconds
        self.undecided = 0                 # songs still waiting for the lyrics search
        self.remembered = {}               # songs an earlier run could not time: {rel: {sig, status, reason}}
        self.speed = opts.speed if opts.speed in self.SPEEDS else "full"
        self.paused = False
        self.timer = None
        self.write_errors = 0              # lyric files in a row that could not be created
        self.only_songs = getattr(opts, "songs", None)    # paths: handle only these songs, not the whole folder
        self.not_usable = []               # [(path as given, why)] of those
        self.unreadable = []
        self.permission_targets = []

    def look(self, unreadable=None):
        """The files this run is about: the whole folder, or only the named songs and what sits next to them."""
        if self.only_songs is None:
            return L.walk(self.source, self.exclude, unreadable)
        files, self.not_usable = L.walk_songs(self.source, self.only_songs, self.exclude, unreadable)
        return files, []

    # ------------------------------------------------------------------ small helpers
    def signature(self, song):
        """Changes whenever the song file, the lyrics that would be used, or this tool changes."""
        try:
            st = os.stat(song.path)
        except OSError:
            return ""
        used = (song.text or "") + "\x00" + (song.found.text if song.found is not None else "") + \
               "\x00" + song.lyrics_url + "\x00" + str(song.explicit)
        return "%d:%d:%s:%s" % (st.st_size, st.st_mtime_ns, hashlib.sha1(used.encode("utf-8", "replace")).hexdigest(), __version__)

    # ------------------------------------------------------------------ how hard to work
    SPEEDS = {"full": (1.0, "full speed"), "half": (0.5, "half speed"), "light": (0.25, "light (a quarter)")}

    def set_speed(self, name=None, pause=None):
        """Can be changed at any moment, also from the keyboard while running."""
        if name in self.SPEEDS:
            self.speed = name
        if pause is not None:
            self.paused = pause
        share, label = self.SPEEDS[self.speed]
        if self.engine is not None:
            self.engine.pace, self.engine.paused = share, self.paused
        decide.low_priority(idle=(self.speed == "light"))
        text = "PAUSED" if self.paused else label
        if self.screen.can_read_keys:
            self.screen.keys = "Keys: 1 full  2 half  3 light  P pause  Ctrl+C stop"
        self.screen.fact("Speed", text + ("  - of what this computer can do" if not self.paused and self.speed != "full" else ""))

    # the same keys on a Persian or Arabic keyboard layout
    OTHER_LAYOUT = {"۱": "1", "۲": "2", "۳": "3", "١": "1", "٢": "2", "٣": "3", "ح": "p"}

    def key(self, ch):
        ch = self.OTHER_LAYOUT.get(ch, ch)
        if ch in ("1", "2", "3"):
            self.set_speed({"1": "full", "2": "half", "3": "light"}[ch], pause=False)
        elif ch == "p":
            self.set_speed(pause=not self.paused)

    def enqueue(self, ready, song):
        old = self.remembered.get(song.rel)
        if old and not self.opts.retry and old.get("sig") == self.signature(song):
            # same file, same lyrics, same tool: listening again would give the same answer
            self.record(song, status=old["status"], reason=old.get("reason", "") + " (as found in an earlier run)", remembered=True)
            self.count(old["status"])
            return
        with self.write_lock:
            self.queued_n += 1
            self.queued_audio += song.seconds or 200.0
        ready.put(song)

    def count(self, key):
        with self.write_lock:
            self.counts[key] = self.counts.get(key, 0) + 1
        names = (("timed", "word-timed"), ("lines_only", "line-timed only"), ("not_timed", "could not be timed"),
                 ("no_lyrics", "no lyrics"), ("rejected", "lyrics rejected"), ("failed", "unreadable"))
        self.screen.tally = "   ".join("%s %d" % (label, self.counts[k]) for k, label in names if self.counts.get(k))

    def record(self, song, **result):
        song.result.update(result)
        if self.log:
            row = {"song": song.rel, "time": datetime.now().isoformat(timespec="seconds")}
            row.update({k: v for k, v in song.result.items()})
            with self.write_lock:
                self.log.write(json.dumps(row, ensure_ascii=False) + "\n")
                self.log.flush()

    def put_file(self, song, ext, text, kind):
        if ext==".elrc" and getattr(self.opts,"repair_lyrics",False):
            from .repair import Publication
            with Publication(self,song,file_bytes(text)) as transaction:
                result=self._put_file(song,ext,text,kind,transaction=transaction)
                if result:transaction.commit()
                return result
        return self._put_file(song,ext,text,kind)

    def _put_file(self, song, ext, text, kind,transaction=None):
        """Publish accepted lyrics; explicit retiming requires a verified original backup."""
        path = song.base + ext
        data = file_bytes(text)
        rec = {"rel": os.path.splitext(song.rel)[0] + ext, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
               "kind": kind, "time": datetime.now().isoformat(timespec="seconds")}
        replacement=bool(ext==".elrc" and getattr(self.opts,"redo",False) and song.retime_path)
        if replacement:
            if not self.backup_rec or not self.backup_rec.get("verified_byte_for_byte"):
                raise OSError("A verified backup is required before retiming existing lyrics")
            previous_rel=os.path.splitext(song.rel)[0]+".elrc"
            saved=os.path.join(self.backup_rec["path"],previous_rel)
            candidate=os.path.join(self.run_dir,"retiming candidates",previous_rel)
            os.makedirs(os.path.dirname(candidate),exist_ok=True)
            if not safe_files.create_new(candidate,data):
                raise OSError("A candidate file already exists; original lyrics were preserved")
            rec.update(previous_sha256=song.retime_sha256,backup=saved,candidate=candidate)
            # Persist recovery information before replacement, including if the process
            # stops between replacing the sidecar and updating the ownership CSV.
            with self.write_lock:
                with open(os.path.join(self.run_dir,"retimed.jsonl"),"a",encoding="utf-8") as f:
                    f.write(json.dumps(rec,ensure_ascii=False)+"\n")
                    f.flush()
                    os.fsync(f.fileno())
            safe_files.replace_backed_up(path,data,saved,song.retime_sha256)
        elif not safe_files.create_new(path,data):
            return False
        if transaction is not None:
            state=os.lstat(path)
            transaction.output_identity=(state.st_dev,state.st_ino)
        with open(path, "rb") as fh:
            if fh.read() != data:
                raise OSError("the lyrics file did not read back as written: " + os.path.basename(path))
        with self.write_lock:
            self.written.append(rec)
            for p in (os.path.join(self.run_dir, "files written.csv"), os.path.join(self.lib_home, "all files written.csv")):
                new = not os.path.exists(p)
                with open(p, "a", newline="", encoding="utf-8") as fh:
                    w = csv.DictWriter(fh, fieldnames=["rel", "sha256", "bytes", "kind", "time"],extrasaction="ignore")
                    if new:
                        w.writeheader()
                    w.writerow(rec)
        return True

    # ------------------------------------------------------------------ stages
    def get_models(self, stage):
        folder = os.path.join(self.home, "models")
        need = models.missing(folder)
        if not need:
            stage.skip("models already here")
            return folder
        stage.start()
        total = sum(m["size"] for m in need)
        os.makedirs(folder, exist_ok=True)
        have = sum(os.path.getsize(os.path.join(folder, m["file"] + ".download")) for m in need
                   if os.path.exists(os.path.join(folder, m["file"] + ".download")))
        try:
            import shutil
            free = shutil.disk_usage(folder).free
        except OSError:
            free = None
        if free is not None and free < total - have + 200 * 1024 * 1024:
            raise Stop("Not enough free space for the models: %s needed in %s, %s free. Nothing was done." % (
                size(total - have), folder, size(free)))
        base = 0
        rate = Rate(30)
        for m in need:
            stage.now = "%s (%s)" % (m["file"], m["what"])

            def progress(done, _total, base=base):
                stage.done, stage.total = base + done, total
                stage.count = "%s of %s" % (size(base + done), size(total))
                rate.add(base + done)
                stage.left = rate.left(total - base - done)

            def note(text, m=m):
                stage.now = "%s, %s" % (m["file"], text)
                if self.log:
                    with self.write_lock:
                        self.log.write(json.dumps({"models": m["file"], "note": text}) + "\n")
                        self.log.flush()
            try:
                models.download(m, folder, progress, self.stop, note=note)
            except models.ModelError as e:
                raise Stop("The models could not be downloaded: %s" % e)
            base += m["size"]
        stage.finish()
        return folder

    def do_backup(self, stage, files, links):
        stage.start()
        place = self.opts.backup_to or backup.default_place(self.source)
        self.screen.fact("Backup", place + "  (copying)")
        rate = Rate(60)

        def progress(done, total, rel):
            stage.done, stage.total, stage.now = done, total, rel
            stage.count = "%s of %s" % (size(done), size(total))
            rate.add(done)
            stage.left = rate.left(total - done)
        try:
            rec = backup.make(self.source, place, files, links, progress, verify=not self.opts.quick_backup, stop=self.stop,
                              part=self.only_songs is not None)
        except backup.BackupError as error:
            from .recovery import permission_error
            if self.opts.backup_to or not permission_error(error):raise
            fallback=os.path.join(self.home,"backups")
            if backup.inside(fallback,self.source):raise
            self.screen.say("BACKUP_FALLBACK: the automatic backup location is not writable; using the app's backup folder.")
            rec=backup.make(self.source,fallback,files,links,progress,verify=not self.opts.quick_backup,stop=self.stop,
                            part=self.only_songs is not None)
        self.backup_rec = rec
        how = "checked byte for byte" if rec["verified_byte_for_byte"] else "sizes checked"
        if rec["linked_to_previous_backup"]:
            how += ", %d unchanged files shared with the previous backup" % rec["linked_to_previous_backup"]
        self.screen.fact("Backup", "%s  (%d files, %s, %s)" % (rec["path"], rec["files_total"], size(rec["bytes_total"]), how))
        stage.finish()
        return rec

    def read_songs(self, stage, audio_files):
        stage.start()
        stage.total = len(audio_files)
        rate = Rate(20)
        out = [None] * len(audio_files)

        def one(i):
            path, rel, sz, _ = audio_files[i]
            try:
                out[i] = L.read_song(path, rel, sz, redo=getattr(self.opts,"redo",False),
                                     retime_mode=getattr(self.opts,"retime_mode","current"),
                                     lyrics_file=getattr(self.opts,"lyrics_file",None),
                                     repair_lyrics=getattr(self.opts,"repair_lyrics",False),
                                     ignore_source=getattr(self.opts,"ignore_lyric_source",False))
            except Exception as e:
                s = L.Song(path=path, rel=rel, size=sz)
                from .recovery import permission_error
                s.skip = "PERMISSION_DENIED: cannot read this song" if permission_error(e) else "could not be read (%s)" % type(e).__name__
                if permission_error(e):self.permission_targets.append(getattr(e,"filename",None) or path)
                out[i] = s
            return rel
        with ThreadPoolExecutor(4) as ex:
            for n, rel in enumerate(ex.map(one, range(len(audio_files))), 1):
                if self.stop.is_set():
                    raise Stop()
                stage.done, stage.now, stage.count = n, rel, "%d of %d" % (n, len(audio_files))
                rate.add(n)
                stage.left = rate.left(len(audio_files) - n)
        self.songs = out
        L.group_same_name(self.songs)
        have = {t: sum(1 for s in self.songs if s.tier == t) for t in L.TIER_NAME}
        stage.finish("%d word-timed already, %d line-timed, %d plain, %d without lyrics" % (
            have[L.WORD], have[L.LINE], have[L.PLAIN], have[L.NONE] + have[L.INSTRUMENTAL]))

    def start_engine(self, models_dir):
        """Load the models while other work goes on."""
        def load():
            try:
                from .engine import Engine
                self.engine = Engine(models_dir, "auto", os.path.join(self.home, "speed test.json"), self.screen.say)
                self.engine.stop = self.stop
                self.screen.fact("Runs on", self.engine.describe())
                self.set_speed()
            except Exception as e:
                try:
                    from .recovery import repair_models
                    self.screen.say("MODEL_LOAD_FAILED: checking the app's model files before one repair attempt.")
                    repaired=repair_models(models_dir,models.MODELS,
                        lambda model,folder:models.download(model,folder,stop=self.stop,note=self.screen.say),
                        say=self.screen.say,stop=self.stop)
                    if not repaired:raise e
                    self.engine=Engine(models_dir,"cpu",None,self.screen.say)
                    self.engine.stop=self.stop
                    self.screen.fact("Runs on",self.engine.describe()+" (repaired model files)")
                    self.set_speed()
                except Exception as failure:self.engine_error=failure
        t = threading.Thread(target=load, daemon=True)
        self.engine_loader=t
        t.start()
        return t

    # ------------------------------------------------------------------ finding lyrics
    def fetcher(self, stage, todo, ready):
        """Runs beside the listening. Every song that ends up with lyrics is handed on to be timed."""
        try:
            if not todo:
                stage.skip("not needed: every song has lyrics")
                return
            if self.opts.offline:
                stage.skip("switched off (--offline)")
                for s in todo:
                    if s.tier in (L.PLAIN, L.LINE) and not s.lyrics_url and not (fetch.wants_uncensored(s) and fetch.censored(s.text)):
                        self.enqueue(ready, s)
                    else:
                        self.no_lyrics(s, "no lyrics, and looking online is switched off")
                return
            stage.start()
            stage.total = self.undecided = len(todo)
            client = fetch.Client(os.path.join(self.lib_home, "lyrics service cache"),
                                  config_path=os.path.join(self.home, "lyrics-providers.json"), refresh=self.opts.retry)
            self.lyrics_client = client
            if client.provider_config.get("configuration_error"):
                self.problems.append("Genius was disabled because lyrics-providers.json could not be read. Check its JSON syntax and value types.")
            rate = Rate(120)
            for n, s in enumerate(todo, 1):
                if self.stop.is_set():
                    return
                stage.now = s.rel
                found, note = fetch.lookup(client, s, self.stop)
                for provider, issue in client.network_failures.items():
                    if issue not in self.problems:
                        self.problems.append(issue)
                        self.screen.say(issue)
                for recovery_note in getattr(client,"recovery_notes",[]):
                    if recovery_note not in self.problems:
                        self.problems.append(recovery_note);self.screen.say(recovery_note)
                s.found, s.fetch_note = found, note
                self.undecided = len(todo) - n
                if found is not None or (s.tier == L.PLAIN and not s.lyrics_url and not (fetch.wants_uncensored(s) and fetch.censored(s.text))):
                    self.enqueue(ready, s)
                elif s.tier == L.INSTRUMENTAL:
                    self.no_lyrics(s, "marked as an instrumental")
                else:
                    self.no_lyrics(s, note)
                stage.done, stage.count = n, "%d of %d" % (n, len(todo))
                rate.add(n)
                stage.left = rate.left(len(todo) - n)
            got = sum(1 for s in todo if s.found is not None)
            stage.finish("found lyrics for %d of %d songs" % (got, len(todo)))
        except Exception as e:                                 # never leave the listener waiting
            self.problems.append("finding lyrics stopped: %r" % (e,))
            stage.skip("stopped by an error (%s)" % type(e).__name__)
        finally:
            self.undecided = 0
            self.fetch_done.set()
            ready.put(None)

    def no_lyrics(self, song, note):
        unavailable = bool(self.lyrics_client and getattr(self.lyrics_client, "network_failures", {}) and
                           any(value in note for value in self.lyrics_client.network_failures.values()))
        status = "network_error" if unavailable else "no_lyrics"
        self.record(song, status=status, reason=note)
        self.count(status)

    # ------------------------------------------------------------------ listening
    def producer(self, ready, work):
        """Helper thread: read the next songs and prepare the voice-separation inputs while the models are busy."""
        def put(item):
            while not self.stop.is_set():
                try:
                    work.put(item, timeout=0.5)
                    return True
                except queue.Full:
                    continue
            return False
        while not self.stop.is_set():
            try:
                song = ready.get(timeout=0.5)
            except queue.Empty:
                continue
            if song is None:
                break
            try:
                mix, stereo = audio.load(song.path)
            except audio.Unusable as e:
                put(("fail", song, str(e)))
                continue
            except Exception as e:
                put(("fail", song, "the audio could not be read (%s)" % type(e).__name__))
                continue
            if not put(("song", song, mix, stereo.shape[1])):
                return
            try:
                for spec in S.spec_batches(stereo):
                    if not put(("spec", spec)):
                        return
            except MemoryError:
                put(("abort", "not enough memory for this song"))
                continue
            del stereo
            put(("end",))
        put(None)

    def listen(self, stage, ready, total_hint):
        """Main thread: the models. Everything else (reading files, the maths before and after the models,
        timing, writing) is done by helper threads so the models never wait."""
        stage.start()
        work = queue.Queue(maxsize=6)
        prod = threading.Thread(target=self.producer, args=(ready, work), daemon=True)
        prod.start()
        pool = ThreadPoolExecutor(3)
        self.start_timer()
        rate = Rate(240)
        state = {"audio": 0.0, "songs": 0}
        cur = None

        def progress():
            done_n, total_n = state["songs"], self.queued_n
            stage.done, stage.total = done_n, max(total_n, 1)
            more = " (+ up to %d still being looked up)" % self.undecided if self.undecided else ""
            stage.count = "%d of %d songs%s" % (done_n, total_n, more)
            stage.left = rate.left(max(self.queued_audio - state["audio"], 0.0)) if total_n > done_n else None
            stage.note = "waiting for lyrics to be found" if total_n <= done_n and not self.fetch_done.is_set() else ""

        while True:
            if self.stop.is_set():
                break
            try:
                item = work.get(timeout=0.5)
            except queue.Empty:
                progress()
                continue
            if item is None:
                break
            kind = item[0]
            if kind == "fail":
                _, song, why = item
                self.record(song, status="failed", reason=why)
                self.count("failed")
                state["songs"] += 1
                state["audio"] += song.seconds or 200.0
            elif kind == "song":
                _, song, mix, n44 = item
                cur = {"song": song, "mix": mix, "n": n44, "parts": [], "t0": time.time(), "bad": None}
                stage.now = song.rel
            elif kind == "abort" and cur:
                cur["bad"] = item[1]
            elif kind == "spec" and cur and not cur["bad"]:
                try:
                    res = self.engine.separate(item[1])
                    cur["parts"].append(pool.submit(S.back, res))
                except Exception as e:
                    cur["bad"] = "the models failed on this song (%s)" % type(e).__name__
            elif kind == "end" and cur:
                song = cur["song"]
                try:
                    if cur["bad"]:
                        raise RuntimeError(cur["bad"])
                    voc = audio.to_mono_16k(S.join([p.result() for p in cur["parts"]], cur["n"]))
                    feat = self.engine.hear(cur["mix"], voc)
                    song.result["listen_s"] = round(time.time() - cur["t0"], 1)
                    self.finish_song(song, feat)
                except Exception as e:
                    self.record(song, status="failed", reason=str(e) if cur["bad"] else "the models failed on this song (%s)" % type(e).__name__)
                    self.count("failed")
                state["audio"] += song.seconds or 200.0
                state["songs"] += 1
                rate.add(state["audio"])
                cur = None
            progress()
        stage.now = "finishing the last songs"
        stage.left, stage.note = None, ""
        self.timer.shutdown(wait=True, cancel_futures=self.stop.is_set())
        pool.shutdown(wait=False)
        stage.done, stage.total = state["songs"], max(state["songs"], 1)
        stage.count = "%d songs" % state["songs"]
        if self.stop.is_set():
            stage.skip("stopped after %d songs" % state["songs"])
        else:
            stage.finish()

    # ------------------------------------------------------------------ deciding and writing (helper thread)
    def attempts(self, song):
        """Which lyrics to try, best first: [(mode, text, whose)]"""
        f, out = song.found, []
        if song.lyrics_url:
            return [("plain",f.text,"found",True)] if f is not None and f.pinned else []
        masked = fetch.wants_uncensored(song) and fetch.censored(song.text)
        if masked:
            if f is not None and fetch.text_sim(f.text, song.text) >= 0.60:
                out.append(("line" if f.tier == L.LINE else "plain", f.text, "found", True))
            return out
        if song.tier == L.LINE:
            out.append(("line", song.text, "yours"))
        elif song.tier == L.PLAIN:
            if f is not None and f.tier == L.LINE and fetch.text_sim(f.text, song.text) >= 0.60:
                out.append(("line", f.text, "found"))          # same words as yours, but with line times
            elif f is not None and f.tier == L.PLAIN and fetch.text_sim(f.text, song.text) >= 0.60:
                out.append(("plain", f.text, "found", f.require_audio_match))
            out.append(("plain", song.text, "yours"))
        elif f is not None:
            out.append(("line" if f.tier == L.LINE else "plain", f.text, "found", f.require_audio_match))
        return out

    def start_timer(self):
        """The word timing is plain calculation. It gets a helper process of its own so it cannot slow the
        listening down; if that cannot be started, a helper thread does the same job."""
        try:
            from concurrent.futures import ProcessPoolExecutor
            self.timer = ProcessPoolExecutor(1, initializer=decide.init)
            self.timer.submit(int, 0).result(timeout=60)
        except Exception:
            self.timer = ThreadPoolExecutor(1)

    def finish_song(self, song, feat):
        """Hand a song that has been listened to over for timing; the answer is written out by apply()."""
        feat = {k: v for k, v in feat.items() if not k.startswith("_")}
        try:
            fut = self.timer.submit(decide.judge, feat, self.attempts(song))
        except Exception:                                      # the helper process is gone: carry on with a thread
            self.timer = ThreadPoolExecutor(1)
            fut = self.timer.submit(decide.judge, feat, self.attempts(song))
        fut.add_done_callback(lambda f, song=song, feat=feat: self.apply(song, f, feat))

    def apply(self, song, fut, feat=None):
        try:
            result = fut.result()
            tried = []
            # Failed online words can try another source using this SAME hearing. No extra GPU pass.
            while (feat is not None and self.lyrics_client is not None and not self.opts.offline
                   and not self.stop.is_set() and song.found is not None and not result["timed"]
                   and result.get("last") and result["last"].get("lyrics") == "found"):
                tried.append(song.found.provider)
                if len(set(tried)) >= 2:
                    break
                alternative, note = fetch.lookup(self.lyrics_client, song, self.stop, skip=tried)
                if alternative is None:
                    break
                song.found = alternative
                result = decide.judge(feat, self.attempts(song))
            if tried:
                song.result["providers_retried"] = tried
            self._apply(song, result)
            self.write_errors = 0
        except OSError as e:                                   # the lyrics file could not be created
            if self.stop.is_set():
                return
            why = e.strerror or str(e)[:200] or type(e).__name__
            from .recovery import permission_error
            if permission_error(e):
                why="PERMISSION_DENIED: "+why
                target=getattr(e,"filename",None)
                self.permission_targets.append(target if target and target.lower().endswith(".elrc") else os.path.dirname(song.path))
            # "remembered" keeps this out of the list of songs not to try again: the song itself is fine
            self.record(song, status="failed", remembered=True,
                        reason="the lyrics file could not be created next to the song (%s)" % why)
            self.count("failed")
            self.write_errors += 1
            if self.write_errors >= 5:                         # not one song's trouble: do not listen on for hours
                self.problems.append("The run stopped itself: lyric files cannot be created in the music folder (%s). "
                                     "Is the folder or drive read-only, or full? Nothing was changed; run again when "
                                     "it can be written to." % why)
                self.screen.say("Stopping: lyric files cannot be created in the music folder (%s)." % why)
                self.stop.set()
        except Exception as e:
            if self.stop.is_set():
                return                                         # stopped by the user: the song counts as not reached
            self.record(song, status="failed", reason="unexpected error while timing (%s: %s)" % (type(e).__name__, str(e)[:80]))
            self.count("failed")

    def _apply(self, song, res):
        def named(info):
            info = dict(info)
            if info.get("lyrics") == "yours":
                info["lyrics_from"] = song.lyrics_from
            elif song.found.pinned:
                info["lyrics_from"] = "%s (chosen page)" % song.found.provider
            else:
                info["lyrics_from"] = "%s record %s" % (song.found.provider, song.found.record)
            if info.get("lyrics") == "found" and song.found.url:
                info["lyrics_url"] = song.found.url
            return info
        if res["timed"]:
            info = named(res["timed"])
            elrc = info.pop("elrc")
            if not self.put_file(song, ".elrc", elrc, "word-timed lyrics"):
                self.record(song, **dict(info, status="skipped", reason="an .elrc file appeared while the run was going"))
                return
            files = [".elrc"]
            if (info["lyrics"] == "found" or info["mode"] == "plain") and not song.has_lrc and not self.opts.no_lrc:
                if self.put_file(song, ".lrc", T.line_level(elrc), "line-timed lyrics"):
                    files.append(".lrc")
            self.record(song, files=files, **info)
            self.count("timed")
            return
        last, keep = res["last"], res["keep_lines"]
        if last is None:
            self.no_lyrics(song, song.fetch_note or "no lyrics")
            return
        if keep and not song.has_lrc and not self.opts.no_lrc and self.put_file(song, ".lrc", keep[0], "line-timed lyrics"):
            self.record(song, **dict(named(keep[1]), status="lines_only", files=[".lrc"]))
            self.count("lines_only")
            return
        if last["lyrics"] == "found" and last["status"] == "not_timed":
            self.save_found(song)
        self.record(song, **named(last))
        self.count(last["status"])

    def save_found(self, song):
        """Lyrics that were found but could not be timed are kept in the run folder (not in the music folder),
        so they can be used by hand."""
        try:
            p = os.path.join(self.run_dir, "lyrics found but not used", os.path.splitext(song.rel)[0] + ".txt")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(song.found.text)
        except OSError:
            pass

    # ------------------------------------------------------------------ after the run
    def final_check(self, stage, before):
        """Compare the music folder with how it was before: nothing missing, nothing changed, and the only
        new files are the lyric files this run wrote."""
        stage.start()
        files, _ = self.look()
        now = {rel: (sz, mt) for _, rel, sz, mt in files}
        mine = {os.path.normcase(w["rel"]) for w in self.written}
        missing = sorted(r for r in before if r not in now)
        archived=getattr(self,"lyric_archives",[])
        archived_originals={row["rel"] for row in archived if os.path.isfile(os.path.join(self.source,row["inactive_rel"])) and safe_files.sha256(os.path.join(self.source,row["inactive_rel"]))==row["sha256"]}
        missing=[r for r in missing if r not in archived_originals]
        mine.update(os.path.normcase(row["inactive_rel"]) for row in archived if row["rel"] in archived_originals)
        changed = sorted(r for r in before if r in now and tuple(before[r]) != now[r])
        replaced=[]
        for w in self.written:
            if w.get("previous_sha256") and w["rel"] in changed:
                if safe_files.sha256(os.path.join(self.source,w["rel"]))==w["sha256"]:
                    replaced.append(w["rel"])
        changed=[r for r in changed if r not in replaced]
        rolled_back=getattr(self,"lyric_rollbacks",{})
        changed=[r for r in changed if r not in rolled_back or safe_files.sha256(os.path.join(self.source,r))!=rolled_back[r]]
        new = sorted(r for r in now if r not in before)
        foreign = [r for r in new if os.path.normcase(r) not in mine]
        lost = sorted(w["rel"] for w in self.written if w["rel"] not in now)
        self.check = {"files_before": len(before), "files_now": len(now), "missing": missing, "changed": changed,
                      "new_by_this_run": len(new) - len(foreign), "new_not_by_this_run": foreign, "written_but_gone": lost,
                      "retimed":replaced,"clean": not missing and not changed and not foreign and not lost}
        self.check["archived_incorrect_lyrics"]=sorted(archived_originals)
        if self.check["clean"]:
            stage.finish("original audio preserved; %d lyric files written, %d explicitly retimed" % (len(self.written),len(replaced)))
        else:
            stage.finish("see the report: %d missing, %d changed by another program, %d new files that are not from this run" % (
                len(missing), len(changed), len(foreign)))

    # ------------------------------------------------------------------ the whole thing
    def run(self):
        o, sc = self.opts, self.screen
        os.makedirs(self.run_dir, exist_ok=True)
        from . import resume
        resume.save(self)
        memo = os.path.join(self.lib_home, "could not be timed.json")
        try:
            self.remembered = json.load(open(memo, encoding="utf-8"))
        except (OSError, ValueError):
            self.remembered = {}
        self.log = open(os.path.join(self.run_dir, "log.jsonl"), "a", encoding="utf-8")
        sc.fact("Music", self.source)
        st_models = sc.stage("Get the models")
        st_backup = sc.stage("Backup copy")
        st_read = sc.stage("Read your songs")
        st_fetch = sc.stage("Find lyrics")
        st_time = sc.stage("Time the words")
        st_check = sc.stage("Final check")
        sc.on_key = self.key
        self.set_speed()
        sc.start()
        ft = None
        try:
            unreadable = []
            files, links = self.look(unreadable)
            self.unreadable = unreadable
            for path in unreadable:self.permission_targets.append(os.path.abspath(os.path.join(self.source,path)))
            few = self.only_songs is not None
            for given, why in self.not_usable:
                self.problems.append("Left out: %s (%s)" % (given, why))
            if unreadable:
                self.problems.append("%d files or folders could not be read (no permission?). They were left alone and are "
                                     "not in the backup copy: %s%s" % (len(unreadable), ", ".join(unreadable[:10]),
                                                                       " ..." if len(unreadable) > 10 else ""))
            audio_files = [f for f in files if os.path.splitext(f[0])[1].lower() in L.AUDIO_EXT]
            if o.only:
                audio_files = [f for f in audio_files if any(x.lower() in f[1].lower() for x in o.only)]
            if o.limit:
                audio_files = audio_files[: o.limit]
            resume.save(self,paths=[row[0] for row in audio_files] if audio_files else self.only_songs)
            sc.fact("Music", "%s  (%d songs, %s in %d files)" % (self.source, len(audio_files), size(sum(f[2] for f in files)), len(files)))
            if not audio_files and few:
                raise Stop("None of the songs given is a song file inside %s. Nothing was done." % self.source)
            if not audio_files:
                raise Stop("No songs were found in this folder (or its subfolders). Nothing was done.\n"
                           "Looked for: %s" % " ".join(sorted(x[1:] for x in L.AUDIO_EXT)))
            # a whole folder: the models load while the backup runs. A few songs: the models are only
            # fetched and loaded once it is clear that there is something to listen to
            engine_thread = None
            rec = self.do_backup(st_backup, files, links)
            before = rec["files"]
            self.read_songs(st_read, audio_files)
            resume.save(self)
            for s in self.songs:
                if s.skip:
                    status = "failed" if s.skip.startswith(("could not be read","PERMISSION_DENIED")) else "not_timed" if s.skip.startswith("invalid ") else "skipped"
                    self.record(s, status=status, reason=s.skip)
            chosen_words=[s for s in self.songs if not s.skip and not s.lyrics_url and s.tier in (L.PLAIN,L.LINE) and
                          (getattr(o,"lyrics_file",None) or (getattr(o,"redo",False) and getattr(o,"retime_mode","current")!="fresh"))]
            for s in chosen_words:
                if fetch.wants_uncensored(s) and fetch.censored(s.text):
                    s.skip="current lyrics are censored; choose fresh lyrics or another source"
                    self.record(s,status="not_timed",reason=s.skip)
            direct=[s for s in chosen_words if not s.skip]
            todo_listen = direct+[s for s in self.songs if not s.skip and s not in chosen_words and s.tier==L.LINE and not fetch.needs_lookup(s)]
            todo_fetch = [s for s in self.songs if not s.skip and s not in chosen_words and fetch.needs_lookup(s)]
            ready = queue.Queue()
            for s in todo_listen:
                self.enqueue(ready, s)
            ft = threading.Thread(target=self.fetcher, args=(st_fetch, todo_fetch, ready), daemon=True)
            ft.start()
            if engine_thread is None:
                while ft.is_alive() and not self.queued_n:
                    ft.join(0.2)
                    if self.stop.is_set():
                        raise Stop()
                if self.queued_n:
                    engine_thread = self.start_engine(self.get_models(st_models))
                else:
                    st_models.skip("not needed")
                    st_time.skip("no song to listen to")
            if engine_thread is not None:
                st_time.note = "loading the models"
                while engine_thread.is_alive():
                    engine_thread.join(0.5)
                    if self.stop.is_set():
                        raise Stop()
                if self.engine_error is not None:
                    raise self.engine_error
                st_time.note = ""
                try:
                    self.listen(st_time, ready, len(todo_listen))
                except KeyboardInterrupt:              # Ctrl+C: stop tidily, still check the folder and write the report
                    self.interrupted = True
                    self.stop.set()
                    st_time.skip("stopped by you")
            if self.stop.is_set():
                self.interrupted = True
            ft.join(timeout=5)
            for s in self.songs:                       # anything not reached (the run was stopped early)
                if not s.result:
                    self.record(s, status="not_reached", reason="the run was stopped before this song")
            self.final_check(st_check, before)
        finally:
            self.stop.set()
            if ft is not None and ft.is_alive():
                ft.join(timeout=15)  # bounded provider requests; finish logging before closing its file
            if self.engine_loader is not None and self.engine_loader.is_alive():
                self.stop.set()
                self.engine_loader.join()
            sc.stop()
            self.log.close()
            for s in self.songs:
                st = s.result.get("status")
                if st in ("not_timed", "rejected", "failed") and not s.result.get("remembered"):
                    self.remembered[s.rel] = {"sig": self.signature(s), "status": st, "reason": s.result.get("reason", "")}
                elif st == "timed":
                    self.remembered.pop(s.rel, None)
            try:
                with open(memo, "w", encoding="utf-8") as fh:
                    json.dump(self.remembered, fh, ensure_ascii=False, indent=0)
            except OSError:
                pass
        return self
