"""One run over a music folder: backup -> read songs -> find lyrics -> listen and time the words -> check.

Safety rules that hold everywhere in this file
  * Songs and every other existing file are only read. Nothing is changed, moved, renamed or removed.
  * The only things written into the music folder are NEW lyric files next to a song (<song>.elrc with
    word timing, and <song>.lrc for songs that had no line-timed lyrics file). A file that already
    exists is never overwritten: files are created in "new file only" mode.
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

from . import __version__, audio, backup, decide, fetch, models
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
        self.run_dir = os.path.join(self.lib_home, "run " + self.started.strftime("%Y-%m-%d %H.%M.%S"))
        self.stop = threading.Event()
        self.screen = Screen("WordLyrics - word-by-word lyrics for your music", plain=opts.plain_output)
        self.songs = []
        self.written = []                  # [{"rel", "sha256", "bytes", "kind"}]
        self.write_lock = threading.Lock()
        self.backup_rec = None
        self.engine = None
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

    # ------------------------------------------------------------------ small helpers
    def signature(self, song):
        """Changes whenever the song file, the lyrics that would be used, or this tool changes."""
        try:
            st = os.stat(song.path)
        except OSError:
            return ""
        used = (song.text or "") + "\x00" + (song.found.text if song.found is not None else "")
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
        """Create <song><ext> next to the song. New file only. -> True when written."""
        path = song.base + ext
        data = file_bytes(text)
        try:
            with open(path, "xb") as fh:                   # "x": fails if the file exists, so nothing is ever overwritten
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
        except FileExistsError:
            return False
        with open(path, "rb") as fh:
            if fh.read() != data:
                raise OSError("the lyrics file did not read back as written: " + os.path.basename(path))
        rec = {"rel": os.path.splitext(song.rel)[0] + ext, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
               "kind": kind, "time": datetime.now().isoformat(timespec="seconds")}
        with self.write_lock:
            self.written.append(rec)
            for p in (os.path.join(self.run_dir, "files written.csv"), os.path.join(self.lib_home, "all files written.csv")):
                new = not os.path.exists(p)
                with open(p, "a", newline="", encoding="utf-8") as fh:
                    w = csv.DictWriter(fh, fieldnames=["rel", "sha256", "bytes", "kind", "time"])
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
        rec = backup.make(self.source, place, files, links, progress, verify=not self.opts.quick_backup, stop=self.stop)
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
                out[i] = L.read_song(path, rel, sz)
            except Exception as e:
                s = L.Song(path=path, rel=rel, size=sz)
                s.skip = "could not be read (%s)" % type(e).__name__
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
                self.engine_error = e
        t = threading.Thread(target=load, daemon=True)
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
                    if s.tier == L.PLAIN:
                        self.enqueue(ready, s)
                    else:
                        self.no_lyrics(s, "no lyrics, and looking online is switched off")
                return
            stage.start()
            stage.total = self.undecided = len(todo)
            client = fetch.Client(os.path.join(self.lib_home, "lyrics service cache"))
            rate = Rate(120)
            pauses = 0
            for n, s in enumerate(todo, 1):
                if self.stop.is_set():
                    return
                stage.now = s.rel
                found, note = None, "the lyrics service could not be reached"
                if pauses < 3:
                    found, note = fetch.lookup(client, s, self.stop)
                    if client.errors_in_a_row >= 6:           # the connection is gone: wait a little, then go on
                        pauses += 1
                        self.screen.say("The lyrics service does not answer. Waiting a minute (%d of 3) ..." % pauses)
                        self.stop.wait(60)
                        client.errors_in_a_row = 0
                        if pauses >= 3:
                            self.screen.say("Giving up on looking for lyrics online for the remaining songs. Run again later to retry them.")
                s.found, s.fetch_note = found, note
                self.undecided = len(todo) - n
                if found is not None or s.tier == L.PLAIN:
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
        self.record(song, status="no_lyrics", reason=note)
        self.count("no_lyrics")

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
        if song.tier == L.LINE:
            out.append(("line", song.text, "yours"))
        elif song.tier == L.PLAIN:
            if f is not None and f.tier == L.LINE and fetch.text_sim(f.text, song.text) >= 0.60:
                out.append(("line", f.text, "found"))          # same words as yours, but with line times
            out.append(("plain", song.text, "yours"))
        elif f is not None:
            out.append(("line" if f.tier == L.LINE else "plain", f.text, "found"))
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
        fut.add_done_callback(lambda f, song=song: self.apply(song, f))

    def apply(self, song, fut):
        try:
            self._apply(song, fut.result())
            self.write_errors = 0
        except OSError as e:                                   # the lyrics file could not be created
            if self.stop.is_set():
                return
            why = e.strerror or type(e).__name__
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
            info["lyrics_from"] = song.lyrics_from if info.get("lyrics") == "yours" else "lrclib.net record %s" % song.found.record
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
        files, _ = L.walk(self.source, self.exclude)
        now = {rel: (sz, mt) for _, rel, sz, mt in files}
        mine = {os.path.normcase(w["rel"]) for w in self.written}
        missing = sorted(r for r in before if r not in now)
        changed = sorted(r for r in before if r in now and tuple(before[r]) != now[r])
        new = sorted(r for r in now if r not in before)
        foreign = [r for r in new if os.path.normcase(r) not in mine]
        lost = sorted(w["rel"] for w in self.written if w["rel"] not in now)
        self.check = {"files_before": len(before), "files_now": len(now), "missing": missing, "changed": changed,
                      "new_by_this_run": len(new) - len(foreign), "new_not_by_this_run": foreign, "written_but_gone": lost,
                      "clean": not missing and not changed and not foreign and not lost}
        if self.check["clean"]:
            stage.finish("nothing missing, nothing changed, %d new lyric files" % len(self.written))
        else:
            stage.finish("see the report: %d missing, %d changed by another program, %d new files that are not from this run" % (
                len(missing), len(changed), len(foreign)))

    # ------------------------------------------------------------------ the whole thing
    def run(self):
        o, sc = self.opts, self.screen
        os.makedirs(self.run_dir, exist_ok=True)
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
        try:
            unreadable = []
            files, links = L.walk(self.source, self.exclude, unreadable)
            if unreadable:
                self.problems.append("%d files or folders could not be read (no permission?). They were left alone and are "
                                     "not in the backup copy: %s%s" % (len(unreadable), ", ".join(unreadable[:10]),
                                                                       " ..." if len(unreadable) > 10 else ""))
            audio_files = [f for f in files if os.path.splitext(f[0])[1].lower() in L.AUDIO_EXT]
            if o.only:
                audio_files = [f for f in audio_files if any(x.lower() in f[1].lower() for x in o.only)]
            if o.limit:
                audio_files = audio_files[: o.limit]
            sc.fact("Music", "%s  (%d songs, %s in %d files)" % (self.source, len(audio_files), size(sum(f[2] for f in files)), len(files)))
            if not audio_files:
                raise Stop("No songs were found in this folder (or its subfolders). Nothing was done.\n"
                           "Looked for: %s" % " ".join(sorted(x[1:] for x in L.AUDIO_EXT)))
            models_dir = self.get_models(st_models)
            engine_thread = self.start_engine(models_dir)
            rec = self.do_backup(st_backup, files, links)
            before = rec["files"]
            self.read_songs(st_read, audio_files)
            for s in self.songs:
                if s.skip:
                    self.record(s, status="skipped", reason=s.skip)
            todo_listen = [s for s in self.songs if not s.skip and s.tier == L.LINE]
            todo_fetch = [s for s in self.songs if not s.skip and s.tier == L.PLAIN] + \
                         [s for s in self.songs if not s.skip and s.tier in (L.NONE, L.INSTRUMENTAL)]
            ready = queue.Queue()
            for s in todo_listen:
                self.enqueue(ready, s)
            ft = threading.Thread(target=self.fetcher, args=(st_fetch, todo_fetch, ready), daemon=True)
            ft.start()
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
            except KeyboardInterrupt:                  # Ctrl+C: stop tidily, still check the folder and write the report
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
