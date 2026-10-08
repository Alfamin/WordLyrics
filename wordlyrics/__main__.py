"""WordLyrics command line.

  WordLyrics                      opens the numbered menu
  WordLyrics "D:\\Music"           opens the menu for that folder
  WordLyrics "D:\\Music" --dry-run only look and say what would be done
  WordLyrics "D:\\Music\\song.flac" only that song (several songs may be given)
  WordLyrics undo "D:\\Music"      take out again what the tool wrote
  WordLyrics check                is everything installed, and how fast is this computer
  WordLyrics setup                fetch the models now instead of at the first run, then check
  WordLyrics providers            show lyric sources and local configuration status (no keys)
  WordLyrics source SONG URL      choose a specific public Genius page for one song
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from . import __version__

HOME = os.environ.get("WORDLYRICS_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def keep_awake(on):
    """Windows: do not let the computer go to sleep in the middle of a long run (the screen may still turn off)."""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000001 if on else 0x80000000)
        except Exception:
            pass


_only_one = []


def already_running():
    """Only one run at a time on a computer: two would fight over the graphics card's memory.
    The mark disappears by itself when the program ends, however it ends."""
    if _only_one or os.name != "nt":
        return False
    try:
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        handle = k32.CreateMutexW(None, False, "WordLyrics-one-run-at-a-time")
        if handle and ctypes.get_last_error() == 183:        # ERROR_ALREADY_EXISTS
            k32.CloseHandle.argtypes=[ctypes.c_void_p]
            k32.CloseHandle(handle)
            return True
        _only_one.append(handle)                             # kept until the program ends
    except Exception:
        pass
    return False


def release_running():
    import gc
    gc.collect()
    if os.name=="nt" and _only_one:
        import ctypes
        close=ctypes.WinDLL("kernel32",use_last_error=True).CloseHandle
        close.argtypes=[ctypes.c_void_p]
        for handle in _only_one:
            close(handle)
    _only_one.clear()


def ask(prompt):
    try:
        return input(prompt).strip().strip('"').strip()
    except EOFError:
        return ""


def parser():
    ap = argparse.ArgumentParser(prog="WordLyrics", description="Word-by-word timed lyrics for a local music folder.")
    ap.add_argument("folder", nargs="*", help="the music folder (subfolders are included), or one or more song files")
    ap.add_argument("--songs-from", metavar="FILE", help="handle only the songs listed in this text file (one path per line, inside "
                                                           "the music folder); only they and their lyric files go into the backup copy")
    ap.add_argument("--result", metavar="FILE", help="also write what happened to each song into this file (JSON), for other programs")
    ap.add_argument("--backup-to", metavar="FOLDER", help="where the backup copy goes (default: the drive with the most free space)")
    ap.add_argument("--yes", action="store_true", help="do not ask anything, use the defaults")
    ap.add_argument("--dry-run", action="store_true", help="only look at the folder and say what would be done; nothing is copied or written")
    ap.add_argument("--offline", action="store_true", help="do not look for lyrics online; only use lyrics that are already there")
    ap.add_argument("--speed", choices=["full", "half", "light"], default=None,
                    help="how much of this computer's power to use: full (best when you are away), half, or light "
                         "(about a quarter, for while you use the computer). Can be changed with the keys 1, 2, 3 during the run")
    ap.add_argument("--no-lrc", action="store_true", help="write only .elrc files, never an extra line-timed .lrc")
    ap.add_argument("--quick-backup", action="store_true", help="backup without reading every copied file back (faster, less thorough)")
    ap.add_argument("--retry", action="store_true", help="try again the songs an earlier run could not time (normally skipped while nothing about them changed)")
    ap.add_argument("--redo",action="store_true",help="explicitly retime existing Enhanced LRC after a verified backup")
    ap.add_argument("--retime-mode",choices=["current","guided","fresh"],default="current")
    ap.add_argument("--confirm-redo",action="store_true",help="confirmation supplied by the interactive menu for a library rerun")
    ap.add_argument("--lyrics-file",help="use this plain lyric file for exactly one selected song")
    ap.add_argument("--only", action="append", default=[], metavar="TEXT", help="only songs whose path contains this text (may be repeated)")
    ap.add_argument("--limit", type=int, default=0, metavar="N", help="only the first N songs (for a trial)")
    ap.add_argument("--no-open", action="store_true", help="do not open the report when done")
    ap.add_argument("--plain-output", action="store_true", help="simple progress lines instead of the live screen")
    ap.add_argument("--version", action="version", version="WordLyrics " + __version__)
    return ap


def dry_run(source, opts):
    from concurrent.futures import ThreadPoolExecutor
    from . import backup
    from . import library as L
    from .ui import size
    print("Looking at %s (nothing is copied or written) ..." % source, flush=True)
    files, links = L.walk(source, [HOME]) if opts.songs is None else (L.walk_songs(source, opts.songs, [HOME])[0], [])
    audio =[f for f in files if os.path.splitext(f[0])[1].lower() in L.AUDIO_EXT]
    if opts.only:
        audio = [f for f in audio if any(x.lower() in f[1].lower() for x in opts.only)]
    if opts.limit:
        audio = audio[: opts.limit]

    def one(f):
        try:
            return L.read_song(f[0], f[1], f[2])
        except Exception as e:
            s = L.Song(path=f[0], rel=f[1], size=f[2])
            s.skip = "could not be read (%s)" % type(e).__name__
            return s
    with ThreadPoolExecutor(4) as ex:
        songs = list(ex.map(one, audio))
    L.group_same_name(songs)
    go = [s for s in songs if not s.skip]
    n = lambda t: sum(1 for s in go if s.tier == t)
    place = opts.backup_to or backup.default_place(source)
    total = sum(f[2] for f in files)
    print()
    print("  %5d songs (%s in %d files)" % (len(songs), size(total), len(files)))
    print("  %5d would be left alone:" % (len(songs) - len(go)))
    reasons = {}
    for s in songs:
        if s.skip:
            reasons[s.skip.split(" (")[0]] = reasons.get(s.skip.split(" (")[0], 0) + 1
    for r, c in sorted(reasons.items(), key=lambda x: -x[1]):
        print("           %5d  %s" % (c, r))
    print("  %5d have line-timed lyrics: their words would be timed" % n(L.LINE))
    print("  %5d have plain lyrics: line-timed lyrics would be looked up online, else the plain ones are timed" % n(L.PLAIN))
    print("  %5d have no lyrics: lyrics would be looked up online%s" % (n(L.NONE) + n(L.INSTRUMENTAL), " (switched off by --offline)" if opts.offline else ""))
    hours = sum(s.seconds for s in go) / 3600
    print()
    print("  Music to listen to: %.1f hours. Backup copy would go to %s (%s needed)." % (hours, place, size(total)))
    return 0


def do_undo(args):
    from . import undo
    ap = argparse.ArgumentParser(prog="WordLyrics undo")
    ap.add_argument("folder")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args(args)
    source = os.path.abspath(a.folder)
    lib_home, move, keep = undo.plan(HOME, source)
    if not move and not keep:
        print("Nothing to undo: this tool has no record of writing files into %s" % source)
        return 0
    print("%d lyric files written by this tool would be moved out of %s" % (len(move), source))
    if keep:
        print("%d would be left alone (changed since they were written, or already gone)" % len(keep))
    if not move:
        return 0
    if not a.yes and ask("Type yes to move them: ").lower() != "yes":
        print("Nothing was moved.")
        return 0
    dest, done = undo.apply(lib_home, move)
    print("%d files moved to %s" % (done, dest))
    return 0


def do_check(remember=None):
    from . import models
    print("WordLyrics %s   folder: %s   Python %s" % (__version__, HOME, sys.version.split()[0]))
    ok = True
    for mod in ("numpy", "av", "onnxruntime", "mutagen", "unidecode"):
        try:
            m = __import__(mod)
            print("  ok       %s %s" % (mod, getattr(m, "__version__", getattr(m, "version_string", ""))))
        except Exception as e:
            ok = False
            print("  MISSING  %s (%s)" % (mod, e))
    folder = os.path.join(HOME, "models")
    need = models.missing(folder)
    for m in models.MODELS:
        print("  %s  model %s" % ("to get  " if m in need else "ok      ", m["file"]))
    if ok and not need:
        from .engine import ALIGNER, SEPARATOR, Engine
        eng = Engine(folder, "auto", remember, lambda msg: print("  " + msg, flush=True))
        print("  runs on: %s" % eng.describe())
        for m in (ALIGNER, SEPARATOR):
            sp = eng.speed.get(m) or {}
            print("    %-18s one call: graphics card %s, processor %s" % (
                m, "%.2f s" % sp["gpu"] if sp.get("gpu") else "not usable", "%.2f s" % sp["cpu"] if sp.get("cpu") else "-"))
    return 0 if ok else 1


def do_setup():
    """Fetch the models now (they are otherwise fetched at the first run), then check that everything works."""
    import shutil
    from . import models
    from .ui import size
    folder = os.path.join(HOME, "models")
    need = models.missing(folder)
    if need and already_running():
        print("WordLyrics is already running on this computer (in another window). It fetches the models itself.")
        return 3
    os.makedirs(folder, exist_ok=True)
    total = sum(m["size"] for m in need)
    have = sum(os.path.getsize(os.path.join(folder, m["file"] + ".download")) for m in need
               if os.path.exists(os.path.join(folder, m["file"] + ".download")))
    if need and shutil.disk_usage(folder).free < total - have + 200 * 1024 * 1024:
        print("Not enough free space for the models: %s needed in %s." % (size(total - have), folder))
        return 1
    for m in need:
        print("\n Fetching %s (%s, %s)" % (m["file"], m["what"], size(m["size"])), flush=True)
        shown = [-1]

        def progress(done, whole):
            pct = int(100 * done / whole)
            if pct != shown[0]:
                shown[0] = pct
                print("\r   %3d %%   %s of %s   " % (pct, size(done), size(whole)), end="", flush=True)
        try:
            models.download(m, folder, progress, note=lambda text: print("\n   " + text, flush=True))
        except models.ModelError as e:
            print("\n The models could not be downloaded: %s" % e)
            return 1
        except KeyboardInterrupt:
            print("\n Stopped. Start again to continue where it stopped.")
            return 130
        print()
    print()
    return do_check(os.path.join(HOME, "speed test.json"))        # measured once here, so the first run need not


def songs_only(opts):
    """Was the tool pointed at songs instead of at a folder? -> (music folder, [song paths]) or (None, None).
    Songs can be named one by one (dragged onto the program) or in a list file (--songs-from)."""
    given = [os.path.abspath(os.path.expanduser(p)) for p in opts.folder]
    if opts.songs_from:
        from .library import read_list
        if len(given) != 1 or not os.path.isdir(given[0]):
            return None, "--songs-from needs the music folder those songs are in."
        if not os.path.isfile(opts.songs_from):
            return None, "The list of songs is not there: %s" % opts.songs_from
        return given[0], read_list(opts.songs_from)
    if given and all(os.path.isfile(p) for p in given):
        try:
            return os.path.commonpath([os.path.dirname(p) for p in given]), given
        except ValueError:
            return None, "The songs are on different drives. Give the songs of one drive at a time."
    return None, None


def write_result(path, run, code, error=""):
    """What happened, for a program that started this one (the Noctis plugin reads it)."""
    import json
    songs = [{"path": s.path, "song": s.rel, "artist": s.artist, "title": s.title, "status": s.result.get("status", "not_reached"),
              "reason": s.result.get("reason", ""), "files": s.result.get("files", [])} for s in (run.songs if run else [])]
    out = {"wordlyrics": __version__, "exit_code": code, "error": error, "music_folder": run.source if run else "",
           "run_folder": run.run_dir if run else "", "left_out": [{"path": p, "reason": w} for p, w in (run.not_usable if run else [])],
           "notes": list(run.problems) if run else [], "songs": songs}
    try:
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
        os.replace(path + ".tmp", path)
    except OSError as e:
        print("The result file could not be written: %s" % e)


def _main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")       # song names in any script must never stop a run
        except Exception:
            pass
    if argv[:1] == ["undo"]:
        return do_undo(argv[1:])
    if argv[:1] == ["check"]:
        if already_running():
            print("WordLyrics is busy. Check the installation after the current run.")
            return 3
        return do_check()
    if argv[:1] == ["setup"]:
        if already_running():
            print("WordLyrics is busy. Setup will wait until the current run finishes.")
            return 3
        return do_setup()
    if argv[:1]==["menu"] or (not argv and sys.stdin.isatty() and sys.stdout.isatty()):
        from .menu import run_menu
        return run_menu(HOME,argv[1:] if argv[:1]==["menu"] else [],executor=main)
    if argv[:1] == ["source"]:
        from . import files, library as L
        from .sources import genius_song_url
        ap=argparse.ArgumentParser(prog="WordLyrics source",description="Choose an exact public Genius song page; audio validation still applies.")
        ap.add_argument("song")
        ap.add_argument("url")
        args=ap.parse_args(argv[1:])
        path=os.path.abspath(os.path.expanduser(args.song))
        url=genius_song_url(args.url)
        if not os.path.isfile(path) or os.path.islink(path) or os.path.splitext(path)[1].lower() not in L.AUDIO_EXT:
            print("Give an existing song file, not a folder or link.")
            return 2
        if not url:
            print("Give a public HTTPS Genius song lyric page.")
            return 2
        pin=os.path.splitext(path)[0]+".lyrics-source.txt"
        if not files.create_new(pin,(url+"\r\n").encode("utf-8")):
            print("A lyrics-source file already exists; edit it yourself to change the choice.")
            return 2
        print("Saved source choice: "+pin)
        print("Run WordLyrics on that song. Existing word-timed files remain untouched.")
        return 0
    if argv[:1] == ["providers"]:
        from .sources import settings
        path = os.path.join(HOME, "lyrics-providers.json")
        config = settings(path)
        print("Lyric order: LRCLIB -> Genius")
        print("LRCLIB: enabled")
        print("Genius: " + ("disabled" if config.get("genius", True) is False else "enabled (public search/pages; no keys)"))
        print("Local configuration: " + path)
        if config.get("configuration_error"):
            print("The local configuration could not be read; check its JSON syntax and value types.")
            return 2
        return 0
    if argv[:1] == ["run"]:
        argv = argv[1:]
    opts = parser().parse_args(argv)
    interactive = sys.stdin.isatty() and sys.stdout.isatty() and not opts.yes
    if interactive and opts.folder and not opts.dry_run and not opts.redo and not opts.lyrics_file and not opts.songs_from:
        from .menu import run_menu
        return run_menu(HOME,opts.folder,executor=main,options=opts)
    wizard = not opts.folder
    if wizard:
        if not interactive:
            parser().print_help()
            return 2
        print("\n WordLyrics %s - word-by-word lyrics for your music\n" % __version__)
        print(" Your songs are only read. A full backup copy of the folder is made first,")
        print(" and the only thing added to it are new lyric files next to the songs.\n")
        opts.folder = [p for p in [ask(" Music folder (type the path or drag the folder here), then Enter:\n > ")] if p]
    source, opts.songs = songs_only(opts)
    if source is None and opts.songs:
        print(opts.songs)                                    # what is wrong with the songs given
        return 2
    if source is None:
        source = os.path.abspath(os.path.expanduser(opts.folder[0])) if len(opts.folder) == 1 else ""
        if len(opts.folder) > 1:
            print("Give one music folder, or song files. Not there: %s" % ", ".join(p for p in opts.folder if not os.path.exists(p)))
            return 2
        if not source or not os.path.isdir(source):
            print("That is not a folder: %s" % (" ".join(opts.folder) or "(nothing given)"))
            return 2
    if opts.lyrics_file and (opts.songs is None or len(opts.songs)!=1 or not os.path.isfile(opts.lyrics_file)):
        print("A chosen lyric file needs exactly one selected song and an existing text file.")
        return 2
    if opts.songs is None and os.path.dirname(source)==source:
        print("Choose a music folder, rather than an entire drive. Nothing was done.")
        return 2
    if opts.redo:
        if opts.songs is None and not opts.confirm_redo:
            print("Whole-library retiming needs confirmation in the numbered menu. Nothing was done.")
            return 2
        opts.quick_backup=False
        opts.retry=True
    if opts.dry_run:
        return dry_run(source, opts)
    if already_running():
        print("WordLyrics is already running on this computer (in another window). Only one run at a time:")
        print("two would fight over the graphics card. Wait for the other one, or stop it with Ctrl+C.")
        return 3
    from . import backup
    if not opts.backup_to and interactive:
        place = backup.default_place(source)
        try:
            import shutil
            free = shutil.disk_usage(os.path.splitdrive(place)[0] + os.sep if os.name == "nt" else os.path.expanduser("~")).free
            extra = " (%.0f GB free there)" % (free / 1e9)
        except OSError:
            extra = ""
        print("\n The backup copy will go to: %s%s" % (place, extra))
        other = ask(" Press Enter to accept, or type another folder:\n > ")
        if other:
            opts.backup_to = os.path.abspath(os.path.expanduser(other))
        print()
    if not opts.speed and interactive:
        print(" How hard should the computer work? (a share of what THIS computer can do)")
        print("   1  Full speed   - fastest; best when you are away or asleep")
        print("   2  Half speed   - the computer stays comfortable to use")
        print("   3  Light        - about a quarter; for gaming or other heavy use meanwhile")
        print(" You can switch at any time during the run with the keys 1, 2, 3 (and P to pause).")
        pick = ask(" Press Enter for full speed, or type 2 or 3:\n > ")
        opts.speed = {"2": "half", "3": "light", "۲": "half", "۳": "light", "٢": "half", "٣": "light"}.get(pick[:1], "full")
        print()
    opts.speed = opts.speed or "full"
    if opts.backup_to and backup.inside(opts.backup_to, source):
        print("The backup cannot be inside the music folder itself. Choose another place with --backup-to.")
        return 2

    from . import report
    from .pipeline import Run, Stop
    run = Run(source, opts, HOME)
    keep_awake(True)
    code, error = 0, ""
    try:
        run.run()
    except KeyboardInterrupt:
        run.stop.set()
        run.interrupted = True
        code, error = 130, "stopped"
        print("\nStopped.")
    except Stop as e:
        code, error = 1, str(e) or "stopped"
        print("\n" + (str(e) or "Stopped."))
    except backup.BackupError as e:
        code, error = 1, "the backup could not be completed: %s" % e
        print("\nThe backup could not be completed, so nothing else was done: %s" % e)
        print("Nothing was written to your music folder.")
    except Exception as e:
        code, error = 1, "%s: %s" % (type(e).__name__, e)
        run.problems.append("the run ended with an error: %s: %s" % (type(e).__name__, e))
        print("\nThe run ended with an error: %s: %s" % (type(e).__name__, e))
    finally:
        keep_awake(False)
    if run.songs:
        try:
            path = report.write(run)
            print()
            for ln in report.summary_lines(run):
                print(" " + ln)
            print("\n Full report: %s" % path)
            if not opts.no_open and interactive and os.name == "nt":
                try:
                    os.startfile(path)                   # opens the report in the browser
                except OSError:
                    pass
        except Exception as e:
            print("The report could not be written: %s" % e)
    if run.interrupted and code == 0:
        code = 130
    if opts.result:
        write_result(opts.result, run, code, error)
    return code


def main(argv=None):
    try:
        return _main(argv)
    finally:
        release_running()


if __name__ == "__main__":
    t0 = time.time()
    sys.exit(main())
