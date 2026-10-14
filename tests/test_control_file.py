"""Pause and Cancel from the Noctis plugin: the control file (--control). Mock engine, no models, no music."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wordlyrics import __main__ as cli, control, progress
from wordlyrics.engine import Engine
from wordlyrics.pipeline import Run, Stop


def make_run(d):
    root = Path(d) / "music"
    root.mkdir()
    opts = cli.parser().parse_args([str(root), "--yes", "--no-open", "--plain-output"])
    with patch("wordlyrics.pipeline.Screen", return_value=Mock()), patch("wordlyrics.pipeline.decide.low_priority"):
        run = Run(str(root), opts, str(Path(d) / "app"))
    os.makedirs(run.run_dir)
    return run, root


def watcher(d, **extra):
    run = Mock()
    path = Path(d) / "control.txt"
    return control.Control(str(path), run, **extra), run, path


class TheFile(unittest.TestCase):
    def test_pause_and_run_follow_the_word_in_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            c, run, path = watcher(d)
            c.poll()
            run.set_speed.assert_not_called()                       # no file: run, and already running
            path.write_text("pause")
            c.poll()
            run.set_speed.assert_called_once_with(pause=True)
            c.poll()
            self.assertEqual(run.set_speed.call_count, 1)           # the same word again changes nothing
            path.write_text("run\n")
            c.poll()
            self.assertEqual(run.set_speed.call_args.kwargs, {"pause": False})
            run.request_stop.assert_not_called()

    def test_words_are_forgiving_about_case_spaces_and_a_byte_order_mark(self):
        with tempfile.TemporaryDirectory() as d:
            c, run, path = watcher(d)
            path.write_bytes(b"\xef\xbb\xbf  PAUSE \r\n")
            c.poll()
            run.set_speed.assert_called_once_with(pause=True)

    def test_a_missing_file_means_run(self):
        with tempfile.TemporaryDirectory() as d:
            c, run, path = watcher(d)
            path.write_text("pause")
            c.poll()
            path.unlink()
            c.poll()
            self.assertEqual(run.set_speed.call_args.kwargs, {"pause": False})

    def test_an_unreadable_or_unclear_file_means_run_only_after_a_few_looks(self):
        for spoil in ("garbage", "directory"):
            with self.subTest(spoil=spoil), tempfile.TemporaryDirectory() as d:
                c, run, path = watcher(d)
                path.write_text("pause")
                c.poll()
                path.unlink()
                if spoil == "directory":
                    path.mkdir()                                    # cannot be read as a file
                else:
                    path.write_text("banana")
                for _ in range(control.UNCLEAR_LIMIT - 1):
                    c.poll()
                    self.assertEqual(run.set_speed.call_count, 1)   # a file being replaced must not let a pause slip
                c.poll()
                self.assertEqual(run.set_speed.call_args.kwargs, {"pause": False})

    def test_a_clear_word_between_unclear_looks_starts_the_count_again(self):
        with tempfile.TemporaryDirectory() as d:
            c, run, path = watcher(d)
            path.write_text("pause")
            c.poll()
            path.write_text("??")
            for _ in range(control.UNCLEAR_LIMIT - 1):
                c.poll()
            path.write_text("pause")
            c.poll()
            path.write_text("??")
            for _ in range(control.UNCLEAR_LIMIT - 1):
                c.poll()
            self.assertEqual(run.set_speed.call_count, 1)

    def test_stop_is_final(self):
        with tempfile.TemporaryDirectory() as d:
            changed = Mock()
            c, run, path = watcher(d, changed=changed)
            path.write_text("stop")
            c.poll()
            run.request_stop.assert_called_once_with()
            path.write_text("run")
            c.poll()
            path.unlink()
            c.poll()
            run.request_stop.assert_called_once_with()
            run.set_speed.assert_not_called()
            changed.assert_called_once_with()

    def test_the_thread_follows_the_file_and_ends_at_stop(self):
        with tempfile.TemporaryDirectory() as d:
            c, run, path = watcher(d, interval=0.02)
            c.start()
            try:
                path.write_text("pause")
                self.assertTrue(self.until(lambda: run.set_speed.called))
                path.write_text("stop")
                self.assertTrue(self.until(lambda: run.request_stop.called))
                self.assertTrue(self.until(lambda: not c._thread.is_alive()))
            finally:
                c.close()

    def test_a_crash_while_looking_never_ends_the_thread(self):
        with tempfile.TemporaryDirectory() as d:
            c, run, path = watcher(d, interval=0.02)
            path.write_text("pause")
            run.set_speed.side_effect = [RuntimeError("odd"), None]
            c.start()
            try:
                self.assertTrue(self.until(lambda: run.set_speed.call_count >= 1))
                self.assertTrue(c._thread.is_alive())
            finally:
                c.close()

    def until(self, condition, seconds=5):
        end = time.time() + seconds
        while time.time() < end:
            if condition():
                return True
            time.sleep(0.01)
        return condition()


class WhatTheRunDoes(unittest.TestCase):
    def test_pause_reaches_the_engine_and_the_status_file(self):
        with tempfile.TemporaryDirectory() as d, patch("wordlyrics.pipeline.decide.low_priority"):
            run, _ = make_run(d)
            run.engine = SimpleNamespace(pace=1.0, paused=False)
            run.screen.stages = []
            run.screen.notice = ""
            path = Path(d) / "control.txt"
            status = Path(d) / "progress.json"
            writer = progress.Writer(str(status), run)
            c = control.Control(str(path), run, changed=lambda: writer.write(force=True))
            path.write_text("pause")
            c.poll()
            self.assertTrue(run.engine.paused and run.paused)
            self.assertTrue(json.loads(status.read_text(encoding="utf-8"))["paused"])
            path.write_text("run")
            c.poll()
            self.assertFalse(run.engine.paused)
            self.assertFalse(json.loads(status.read_text(encoding="utf-8"))["paused"])

    def test_the_status_file_can_be_forced_and_never_gives_up_its_old_keys(self):
        with tempfile.TemporaryDirectory() as d:
            run = SimpleNamespace(screen=SimpleNamespace(stages=[], notice="x"), songs=[], only_songs=None, run_dir=d)
            status = Path(d) / "progress.json"
            writer = progress.Writer(str(status), run)
            writer.write()
            writer.write(force=True)                                # inside the throttle time, but forced
            data = json.loads(status.read_text(encoding="utf-8"))
            self.assertFalse(data["paused"])
            for key in ("stages", "completed", "total", "notice", "report", "finished", "counts"):
                self.assertIn(key, data)

    def test_a_pause_does_not_hold_back_a_stop(self):
        engine = Engine.__new__(Engine)
        engine.pace, engine.paused, engine.stop = 1.0, True, threading.Event()
        threading.Timer(0.3, engine.stop.set).start()
        begun = time.time()
        engine._rest(0.01)                                           # waits while paused, ends when the run is stopped
        self.assertGreater(time.time() - begun, 0.15)
        self.assertLess(time.time() - begun, 3)

    def test_stop_ends_listening_after_the_step_in_hand_and_shuts_the_helpers(self):
        with tempfile.TemporaryDirectory() as d:
            run, _ = make_run(d)
            song = SimpleNamespace(rel="a.mp3", seconds=10, result={})
            run.engine = Mock()
            run.engine.separate.side_effect = lambda spec: run.request_stop()
            stage = Mock()

            def feed(ready, work):
                for item in [("song", song, None, 10), ("spec", None), ("spec", None), ("end",), None]:
                    work.put(item)

            def start_timer():
                run.timer = ThreadPoolExecutor(1)
            with patch.object(run, "producer", side_effect=feed), patch.object(run, "start_timer", side_effect=start_timer):
                run.listen(stage, queue.Queue(), 0)                  # returns by itself: nothing to catch
            self.assertEqual(run.engine.separate.call_count, 1)
            self.assertTrue(run.stop.is_set() and run.user_stop and run.interrupted)
            self.assertTrue(run.timer._shutdown)
            self.assertIn("stopped after", stage.skip.call_args.args[0])


class TheProgram(unittest.TestCase):
    def test_the_option_exists_but_is_not_advertised(self):
        opts = cli.parser().parse_args(["folder", "--control", "c.txt"])
        self.assertEqual(opts.control, "c.txt")
        self.assertIsNone(cli.parser().parse_args(["folder"]).control)
        self.assertNotIn("--control", cli.parser().format_help())

    def run_main(self, d, fake_run, *extra):
        music = Path(d) / "music"
        music.mkdir(exist_ok=True)
        (music / "one.mp3").write_bytes(b"x")
        result, status = Path(d) / "result.json", Path(d) / "progress.json"
        args = [str(music), "--yes", "--no-open", "--plain-output", "--backup-to", str(Path(d) / "backup"),
                "--result", str(result), "--progress", str(status), *extra]
        with patch.object(cli, "HOME", str(Path(d) / "app")), patch.object(cli, "already_running", return_value=False), \
                patch.object(cli, "keep_awake"), patch.object(Run, "run", fake_run), patch("builtins.print"):
            code = cli.main(args)
        return code, json.loads(result.read_text(encoding="utf-8")) if result.exists() else None

    def test_cancel_in_the_window_ends_like_ctrl_c(self):
        for how in (Stop("No songs were found"), None):
            with self.subTest(ends_with=type(how).__name__), tempfile.TemporaryDirectory() as d:
                ctl = Path(d) / "control.txt"
                ctl.write_text("stop")
                seen = {}

                def fake_run(self, how=how):
                    seen["stopped"] = self.stop.wait(10)             # the control thread did this
                    seen["user"] = self.user_stop
                    if how is not None:
                        raise how
                    return self
                code, result = self.run_main(d, fake_run, "--control", str(ctl))
                self.assertEqual(code, 130)
                self.assertEqual((result["exit_code"], result["error"]), (130, "stopped" if how is not None else ""))
                self.assertEqual(seen, {"stopped": True, "user": True})

    def test_without_the_option_nothing_looks_at_any_file_and_the_usual_exit_codes_stay(self):
        with tempfile.TemporaryDirectory() as d:
            ctl = Path(d) / "control.txt"
            ctl.write_text("stop")

            def fake_run(self):
                self.stop.wait(0.5)
                raise Stop("No songs were found")
            with patch("wordlyrics.control.Control") as watching:
                code, result = self.run_main(d, fake_run)
            watching.assert_not_called()
            self.assertEqual((code, result["exit_code"]), (1, 1))

    def test_a_backup_cut_short_by_cancel_is_a_stop_not_a_failure(self):
        from wordlyrics import backup
        with tempfile.TemporaryDirectory() as d:
            ctl = Path(d) / "control.txt"
            ctl.write_text("stop")

            def fake_run(self):
                self.stop.wait(10)
                raise backup.BackupError("stopped before the backup was complete")
            code, result = self.run_main(d, fake_run, "--control", str(ctl))
            self.assertEqual((code, result["error"]), (130, "stopped"))

    def test_the_watcher_is_closed_when_the_run_is_over(self):
        with tempfile.TemporaryDirectory() as d:
            ctl = Path(d) / "control.txt"
            closed = []
            real = control.Control

            class Spy(real):
                def close(self):
                    closed.append(True)
                    super().close()
            with patch("wordlyrics.control.Control", Spy):
                self.run_main(d, lambda self: self, "--control", str(ctl))
            self.assertEqual(closed, [True])


if __name__ == "__main__":
    unittest.main()
