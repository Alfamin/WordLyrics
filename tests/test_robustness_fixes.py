"""Regressions from the code review: Ctrl+C, helper threads, graphics-card recovery, remembered failures,
busy files, undo, text decoding, very short audio and state files. No models, no real music."""
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
import json
import os
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wordlyrics import __main__ as cli, audio, engine as E, files, library as L, pipeline, recovery, undo
from wordlyrics.engine import Engine
from wordlyrics.pipeline import Run

BUSY = (13, "The process cannot access the file because it is being used by another process", None, 32)


def make_run(d, *extra):
    root = Path(d) / "music"
    root.mkdir()
    opts = cli.parser().parse_args([str(root), "--yes", "--no-open", "--plain-output", *extra])
    with patch("wordlyrics.pipeline.Screen", return_value=Mock()), patch("wordlyrics.pipeline.decide.low_priority"):
        run = Run(str(root), opts, str(Path(d) / "app"))
    os.makedirs(run.run_dir)
    return run, root


@unittest.skipUnless(os.name == "nt", "the retry is for Windows sharing violations")
class BusyFiles(unittest.TestCase):
    def test_rename_is_retried_while_another_program_holds_the_file(self):
        real = os.rename
        calls = []

        def busy_twice(src, dst):
            calls.append(src)
            if len(calls) <= 2:
                raise PermissionError(*BUSY)
            return real(src, dst)
        with tempfile.TemporaryDirectory() as d, patch.object(files.os, "rename", side_effect=busy_twice), \
                patch.object(files.time, "sleep") as pause:
            target = Path(d) / "song.elrc"
            self.assertTrue(files.create_new(target, b"lyrics"))
            self.assertEqual(target.read_bytes(), b"lyrics")
            self.assertEqual(pause.call_count, 2)
            self.assertEqual([p.name for p in Path(d).iterdir()], ["song.elrc"])

    def test_real_access_denied_is_not_retried_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as d, patch.object(files.os, "rename", side_effect=PermissionError(13, "denied", None, 5)) as rename, \
                patch.object(files.time, "sleep") as pause:
            with self.assertRaises(PermissionError):
                files.create_new(Path(d) / "song.elrc", b"lyrics")
            self.assertEqual(rename.call_count, 1)
            pause.assert_not_called()
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_gives_up_after_a_few_tries(self):
        with tempfile.TemporaryDirectory() as d, patch.object(files.os, "rename", side_effect=PermissionError(*BUSY)) as rename, \
                patch.object(files.time, "sleep"):
            with self.assertRaises(PermissionError):
                files.create_new(Path(d) / "song.elrc", b"lyrics")
            self.assertEqual(rename.call_count, 4)
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_a_file_in_use_is_not_a_permission_problem(self):
        self.assertFalse(recovery.permission_error(PermissionError(*BUSY)))
        self.assertFalse(recovery.permission_error(PermissionError(13, "locked", None, 33)))
        self.assertTrue(recovery.permission_error(PermissionError(13, "denied", None, 5)))
        self.assertTrue(recovery.permission_error(PermissionError()))
        try:
            try:
                raise PermissionError(*BUSY)
            except PermissionError as inner:
                raise RuntimeError("wrapped") from inner
        except RuntimeError as wrapped:
            self.assertFalse(recovery.permission_error(wrapped))


class UndoKeepsGoing(unittest.TestCase):
    def test_one_locked_file_does_not_stop_the_rest(self):
        real_replace, real_remove = os.replace, os.remove
        with tempfile.TemporaryDirectory() as d:
            source, lib_home = Path(d) / "music", Path(d) / "app"
            source.mkdir()
            names = ["one.elrc", "locked.elrc", "three.elrc"]
            for name in names:
                (source / name).write_bytes(name.encode())
            move = [(str(source / name), name) for name in names]

            def replace(src, dst):
                if "locked" in str(src):
                    raise PermissionError(*BUSY)
                return real_replace(src, dst)

            def remove(path):
                if "locked" in str(path):
                    raise PermissionError(*BUSY)
                return real_remove(path)
            with patch.object(undo.os, "replace", side_effect=replace), patch.object(undo.os, "remove", side_effect=remove):
                dest, done, failed = undo.apply(str(lib_home), move)
            self.assertEqual(done, 2)
            self.assertEqual([rel for rel, _ in failed], ["locked.elrc"])
            self.assertIn("could not be removed", failed[0][1])
            self.assertTrue((source / "locked.elrc").exists())
            self.assertFalse((source / "one.elrc").exists() or (source / "three.elrc").exists())
            self.assertTrue((Path(dest) / "one.elrc").exists() and (Path(dest) / "three.elrc").exists())

    def test_command_reports_what_was_left(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "music"
            source.mkdir()
            with patch("wordlyrics.undo.plan", return_value=(d, [("x", "x.elrc")], [])), \
                    patch("wordlyrics.undo.apply", return_value=(d, 0, [("x.elrc", "in use")])), \
                    patch("builtins.print") as shown:
                code = cli.do_undo([str(source), "--yes"])
            self.assertEqual(code, 4)
            self.assertIn("x.elrc: in use", " ".join(str(c.args[0]) for c in shown.call_args_list))


class ReadingText(unittest.TestCase):
    def read(self, raw):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "lyrics.lrc"
            path.write_bytes(raw)
            return L._read_text(str(path))

    def test_old_code_page_text_is_not_turned_into_chinese(self):
        raw = b"[00:01.00]Caf\xe9 au lait and more words here\n"
        if len(raw) % 2:
            raw += b" "
        text = self.read(raw)
        self.assertTrue(text.startswith("[00:01.00]Caf"))
        self.assertIn("au lait and more words here", text)           # not the CJK noise a blind UTF-16 read gives

    def test_utf16_with_and_without_a_mark_is_still_read(self):
        latin = "[00:01.00]hello there\n[00:03.00]second line\n"
        persian = "سلام دنیا سلام دنیا\n" * 3
        self.assertEqual(self.read(latin.encode("utf-16")), latin)
        self.assertEqual(self.read(latin.encode("utf-16-le")), latin)
        self.assertEqual(self.read(latin.encode("utf-16-be")), latin)
        self.assertEqual(self.read(persian.encode("utf-16-le")), persian)
        self.assertEqual(self.read(persian.encode("utf-8")), persian)
        self.assertEqual(self.read(persian.encode("utf-8-sig")), persian)


class ShortAudio(unittest.TestCase):
    def test_clip_shorter_than_the_smoothing_window(self):
        rng = np.random.default_rng(1)
        for seconds in (0.02, 0.5, 1.0, 1.2, 1.49):
            voc = (rng.standard_normal(int(16000 * seconds)) * 0.1).astype(np.float32)
            out = E.lift_quiet(voc)
            self.assertEqual(out.shape, voc.shape)
            self.assertEqual(out.dtype, np.float32)

    def test_longer_clip_is_unchanged_in_shape(self):
        voc = (np.random.default_rng(2).standard_normal(16000 * 5) * 0.1).astype(np.float32)
        self.assertEqual(E.lift_quiet(voc).shape, voc.shape)

    def test_decoding_still_gives_float32(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "tone.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(44100)
                w.writeframes((np.sin(np.arange(66150) / 20.0) * 8000).astype("<i2").repeat(2).tobytes())
            mono, stereo = audio.load(str(path))
            self.assertEqual((mono.dtype, stereo.dtype), (np.float32, np.float32))
            self.assertEqual(stereo.shape[0], 2)
            self.assertAlmostEqual(len(mono) / 16000, 1.5, delta=0.05)
            self.assertEqual(audio.to_mono_16k(stereo).dtype, np.float32)


class GraphicsCardComesBack(unittest.TestCase):
    def engine(self):
        e = Engine.__new__(Engine)
        e.where = {E.ALIGNER: "gpu", E.SEPARATOR: "gpu"}
        e.gpu, e.note, e.gpu_failures = "", "", 0
        e.gpu_lost, e.gpu_retry = {}, {}
        e.say, e._rest, e._drop, e._bench, e.changed = Mock(), Mock(), Mock(), Mock(), Mock()
        e.gpu_session, e.cpu_session = Mock(), Mock()
        e._session = Mock(side_effect=lambda model, where: e.gpu_session if where == "gpu" else e.cpu_session)
        return e

    def clock(self, start=1000.0):
        now = [start]
        return now, patch.object(E, "time", SimpleNamespace(monotonic=lambda: now[0], perf_counter=time.perf_counter, sleep=time.sleep))

    def test_processor_stands_in_then_the_card_is_used_again(self):
        e = self.engine()
        e.gpu_session.run.side_effect = RuntimeError("device removed")
        e.cpu_session.run.return_value = ["cpu"]
        now, fake = self.clock()
        with fake:
            self.assertEqual(e._run(E.ALIGNER, None, {}), ["cpu"])
            self.assertEqual(e.where[E.ALIGNER], "cpu")
            self.assertIn("GPU_FALLBACK", e.note)
            self.assertIn("one model", e.describe())
            e.changed.assert_called_once()
            now[0] += 100                                  # not yet time
            self.assertEqual(e._run(E.ALIGNER, None, {}), ["cpu"])
            e._bench.assert_not_called()
            now[0] += 30                                   # 130 s after the failure: time to look
            e.gpu_session.run.side_effect = None
            e.gpu_session.run.return_value = ["gpu"]
            self.assertEqual(e._run(E.ALIGNER, None, {}), ["gpu"])
            e._bench.assert_called_once_with(E.ALIGNER, "gpu")
        self.assertEqual(e.where[E.ALIGNER], "gpu")
        self.assertEqual(e.note, "")
        self.assertEqual(e.gpu_retry, {})
        self.assertEqual(e.describe(), "graphics card")
        self.assertEqual(e.changed.call_count, 2)
        e.changed.assert_called_with("")

    def test_waits_get_longer_while_the_card_keeps_failing(self):
        e = self.engine()
        e.gpu_session.run.side_effect = RuntimeError("device removed")
        e.cpu_session.run.return_value = ["cpu"]
        e._bench.side_effect = RuntimeError("still gone")
        now, fake = self.clock()
        waits = []
        with fake:
            e._run(E.SEPARATOR, None, {})
            waits.append(e.gpu_retry[E.SEPARATOR] - now[0])
            for _ in range(5):
                now[0] = e.gpu_retry[E.SEPARATOR] + 1
                before = now[0]
                self.assertEqual(e._run(E.SEPARATOR, None, {}), ["cpu"])
                waits.append(e.gpu_retry[E.SEPARATOR] - before)
        self.assertEqual(waits, [120, 300, 900, 1800, 1800, 1800])
        self.assertEqual(e.where[E.SEPARATOR], "cpu")

    def test_a_model_that_was_always_on_the_processor_is_left_alone(self):
        e = self.engine()
        e.where[E.ALIGNER] = "cpu"
        e.cpu_session.run.return_value = ["cpu"]
        self.assertEqual(e._run(E.ALIGNER, None, {}), ["cpu"])
        e._bench.assert_not_called()

    def test_run_keeps_the_runs_on_line_true_and_notes_a_fallback_once(self):
        with tempfile.TemporaryDirectory() as d:
            run, _ = make_run(d)
            run.engine = Mock(describe=Mock(return_value="processor"))
            run.engine_event("The graphics card failed.")
            run.engine_event("The graphics card failed.")
            run.engine_event("")
            self.assertEqual(run.problems, ["The graphics card failed."])
            run.screen.fact.assert_called_with("Runs on", "processor")


class HelperThreads(unittest.TestCase):
    def songs(self, run, n):
        return [L.Song(path=str(Path(run.source) / ("%d.mp3" % i)), rel="%d.mp3" % i) for i in range(n)]

    def kinds(self, work):
        out = []
        while not work.empty():
            item = work.get_nowait()
            out.append(item[0] if item else None)
        return out

    def run_producer(self, run, songs, specs):
        ready, work = queue.Queue(), queue.Queue()
        for s in songs:
            ready.put(s)
        ready.put(None)
        audio_load = Mock(return_value=(np.zeros(32000, np.float32), np.zeros((2, 88200), np.float32)))
        with patch("wordlyrics.pipeline.audio.load", audio_load), patch("wordlyrics.pipeline.S.spec_batches", side_effect=specs):
            run.producer(ready, work)
        return work

    def test_any_failure_preparing_a_song_ends_that_song_and_goes_on(self):
        def broken():
            yield "spec"
            raise ValueError("odd shape")

        def memory():
            yield "spec"
            raise MemoryError()
        with tempfile.TemporaryDirectory() as d:
            run, _ = make_run(d)
            work = self.run_producer(run, self.songs(run, 3), [broken(), memory(), iter(["spec"])])
            items = []
            while not work.empty():
                items.append(work.get_nowait())
        kinds = [i[0] if i else None for i in items]
        self.assertEqual(kinds, ["song", "spec", "abort", "end", "song", "spec", "abort", "end", "song", "spec", "end", None])
        self.assertIn("could not be prepared (ValueError)", items[2][1])
        self.assertEqual(items[6][1], "not enough memory for this song")

    def test_the_closing_none_is_always_sent(self):
        with tempfile.TemporaryDirectory() as d:
            run, _ = make_run(d)
            work = queue.Queue()
            with self.assertRaises(RuntimeError):
                run.producer(Mock(get=Mock(side_effect=RuntimeError("unexpected"))), work)
            self.assertEqual(self.kinds(work), [None])

    def listen(self, run, items, timer_jobs=None):
        stage = Mock()
        run.engine = Mock()

        def feed(ready, work):
            for item in items:
                work.put(item)

        def start_timer():
            run.timer = ThreadPoolExecutor(1)
            if timer_jobs:
                timer_jobs(run.timer)
        with patch.object(run, "producer", side_effect=feed), patch.object(run, "start_timer", side_effect=start_timer):
            run.listen(stage, queue.Queue(), 0)
        return stage

    def test_an_aborted_song_is_counted_and_not_remembered(self):
        with tempfile.TemporaryDirectory() as d:
            run, _ = make_run(d)
            song, other = self.songs(run, 2)
            mix = np.zeros(16000, np.float32)
            self.listen(run, [("song", song, mix, 10), ("abort", "not enough memory for this song"), ("end",),
                              ("fail", other, "the audio cannot be opened (X)"), None])
            self.assertEqual(song.result["status"], "failed")
            self.assertTrue(song.result["remembered"])
            self.assertEqual(song.result["reason"], "not enough memory for this song")
            self.assertEqual(other.result["status"], "failed")
            self.assertNotIn("remembered", other.result)            # unreadable audio is still remembered
            self.assertEqual(run.counts["failed"], 2)

    def test_ctrl_c_waits_for_the_song_in_flight_and_drops_the_queue(self):
        with tempfile.TemporaryDirectory() as d:
            run, _ = make_run(d)
            song, = self.songs(run, 1)
            mix = np.zeros(16000, np.float32)
            jobs = {}

            def timer_jobs(timer):
                jobs["running"] = timer.submit(time.sleep, 0.3)
                jobs["queued"] = timer.submit(time.sleep, 0)
                time.sleep(0.1)                              # the first one has started
            run.engine = Mock()
            run.engine.separate.side_effect = KeyboardInterrupt
            stage = Mock()

            def feed(ready, work):
                for item in [("song", song, mix, 10), ("spec", np.zeros(1)), None]:
                    work.put(item)

            def start_timer():
                run.timer = ThreadPoolExecutor(1)
                timer_jobs(run.timer)
            with patch.object(run, "producer", side_effect=feed), patch.object(run, "start_timer", side_effect=start_timer):
                with self.assertRaises(KeyboardInterrupt):
                    run.listen(stage, queue.Queue(), 0)
            self.assertTrue(jobs["running"].done() and not jobs["running"].cancelled())
            self.assertTrue(jobs["queued"].cancelled())
            self.assertTrue(run.stop.is_set())
            self.assertTrue(run.timer._shutdown)
            with self.assertRaises(RuntimeError):
                run.timer.submit(int, 0)                     # nothing can be timed or written any more

    def test_fetcher_reads_a_snapshot_of_the_failures(self):
        class Failures(dict):
            def __init__(self, run):
                super().__init__()
                self.run, self.problems_at_end = run, None

            def items(self):
                yield "LRCLIB", "LRCLIB [HTTP_503]: down"
                self.problems_at_end = len(self.run.problems)       # a live view would be read on after the first was handled
        with tempfile.TemporaryDirectory() as d:
            run, _ = make_run(d)
            failures = Failures(run)
            client = Mock(provider_config={}, network_failures=failures, recovery_notes=[])
            song, = self.songs(run, 1)
            ready = queue.Queue()
            with patch("wordlyrics.pipeline.fetch.Client", return_value=client), \
                    patch("wordlyrics.pipeline.fetch.lookup", return_value=(None, "not in the lyrics database")):
                run.fetcher(Mock(), [song], ready)
            self.assertEqual(failures.problems_at_end, 0)
            self.assertIn("LRCLIB [HTTP_503]: down", run.problems)
            self.assertIsNone(ready.get_nowait())


class WhatIsRemembered(unittest.TestCase):
    def apply_failure(self, error):
        with tempfile.TemporaryDirectory() as d:
            run, root = make_run(d)
            song = L.Song(path=str(root / "a.mp3"), rel="a.mp3")
            done = Future()
            done.set_exception(error)
            run.apply(song, done)
            return song.result

    def test_bugs_and_broken_helpers_are_not_remembered(self):
        for error in (RuntimeError("bug"), BrokenProcessPool("helper died")):
            with self.subTest(error=type(error).__name__):
                result = self.apply_failure(error)
                self.assertEqual(result["status"], "failed")
                self.assertTrue(result["remembered"])
                self.assertIn("unexpected error while timing", result["reason"])

    def test_the_lrc_copy_failing_does_not_undo_a_published_elrc(self):
        with tempfile.TemporaryDirectory() as d:
            run, root = make_run(d)
            song = L.Song(path=str(root / "a.mp3"), rel="a.mp3")
            timed = {"mode": "plain", "lyrics": "yours", "status": "timed", "elrc": "[00:01.00]<00:01.00>a<00:02.00>"}
            with patch.object(run, "put_file", side_effect=[True, OSError(13, "Permission denied")]) as put:
                run._apply(song, {"timed": timed, "keep_lines": None, "last": None})
            self.assertEqual([c.args[1] for c in put.call_args_list], [".elrc", ".lrc"])
            self.assertEqual(song.result["status"], "timed")
            self.assertEqual(song.result["files"], [".elrc"])
            self.assertIn(".lrc copy could not be written", song.result["timing_note"])
            self.assertNotIn("remembered", song.result)
            self.assertEqual(run.counts, {"timed": 1})

    def test_state_file_is_never_half_written(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "could not be timed.json")
            pipeline.save_json(path, {"a": {"sig": "1"}})
            self.assertEqual(json.loads(Path(path).read_text(encoding="utf-8")), {"a": {"sig": "1"}})

            def half(data, fh, **kwargs):
                fh.write('{"b": ')
                raise OSError("disk full")
            with patch.object(pipeline.json, "dump", side_effect=half):
                with self.assertRaises(OSError):
                    pipeline.save_json(path, {"b": 2})
            self.assertEqual(json.loads(Path(path).read_text(encoding="utf-8")), {"a": {"sig": "1"}})
            self.assertEqual(os.listdir(d), ["could not be timed.json"])
            pipeline.save_json(path, {"persian": "سلام"})
            self.assertEqual(json.loads(Path(path).read_text(encoding="utf-8")), {"persian": "سلام"})


if __name__ == "__main__":
    unittest.main()
