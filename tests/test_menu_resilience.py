"""Menu robustness: Ctrl+C, unexpected errors, readable output, exit messages, saved folders, permission helper."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from wordlyrics import library as L, menu, permissions, resume


def make(d, answers, execute=None, folders_saved=(), **kwargs):
    """A menu that never reads Noctis' settings; `answers` are the typed replies, in order."""
    home = Path(d) / "app"
    home.mkdir(exist_ok=True)
    if folders_saved:
        (home / "menu-settings.json").write_text(json.dumps({"libraries": [str(p) for p in folders_saved]}))
    typed = iter(answers)
    with patch.object(menu.folders, "discover", return_value=([], [])):
        m = menu.Menu(str(home), [], execute or Mock(return_value=0), reader=lambda _: next(typed), writer=Mock(), **kwargs)
    return m


def said(m):
    return "\n".join(str(call.args[0]) for call in m.say.call_args_list)


class CtrlCAndUnexpectedErrors(unittest.TestCase):
    def test_ctrl_c_during_an_action_returns_to_the_main_menu(self):
        with tempfile.TemporaryDirectory() as d:
            m = make(d, ["1", "0"])
            m.scan = Mock(side_effect=KeyboardInterrupt)
            self.assertEqual(m.run(), 0)
            self.assertIn("Ctrl+C", m.last_notice)

    def test_an_unexpected_error_is_one_plain_line_and_the_menu_stays_open(self):
        with tempfile.TemporaryDirectory() as d:
            m = make(d, ["1", "0"])
            m.scan = Mock(side_effect=KeyError("surprise"))
            self.assertEqual(m.run(), 0)
            self.assertIn("KeyError", m.last_notice)
            self.assertNotIn("Traceback", said(m))
            self.assertIn("surprise", (m.home / "menu-errors.log").read_text())

    def test_scan_stops_at_ctrl_c_instead_of_reading_the_whole_library(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "music"
            root.mkdir()
            for i in range(30):
                (root / ("%02d.mp3" % i)).write_bytes(b"x")
            m = make(d, [], folders_saved=[root])
            with patch.object(L, "read_song", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    m.scan()
            self.assertIsNone(m.catalog)


class OutputStaysReadable(unittest.TestCase):
    def test_redraw_waits_for_enter_after_a_check_screen(self):
        with tempfile.TemporaryDirectory() as d:
            prompts = []
            answers = iter(["6", "", "0"])
            m = make(d, [], execute=Mock(return_value=0))
            m.color = True                                   # a real terminal: the redraw clears the screen
            m.read = lambda prompt: prompts.append(prompt) or next(answers)
            self.assertEqual(m.run(), 0)
            m.execute.assert_called_once_with(["diagnose"])
            self.assertEqual(sum("Press Enter" in p for p in prompts), 1)
            self.assertEqual(len(prompts), 3)                # menu choice, the pause, then Exit: no pause on the way out

    def test_no_pause_when_nothing_needs_reading_or_the_screen_is_not_cleared(self):
        with tempfile.TemporaryDirectory() as d:
            m = make(d, [])
            reads = Mock(return_value="")
            m.read = reads
            m.color = True
            m.panel("A", fresh=True)
            reads.assert_not_called()
            m.unread = True
            m.color = False
            m.panel("B", fresh=True)
            reads.assert_not_called()

    def test_every_job_result_waits_for_enter_before_the_next_redraw(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "music"
            root.mkdir()
            m = make(d, [], execute=Mock(return_value=4), folders_saved=[root])
            m.job([L.Song(str(root / "one.mp3"), "one.mp3")])
            self.assertTrue(m.unread)


class ExitMessages(unittest.TestCase):
    def notice(self, code):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "music"
            root.mkdir()
            m = make(d, [], execute=Mock(return_value=code), folders_saved=[root])
            m.job([L.Song(str(root / "one.mp3"), "one.mp3")])
            return m.last_notice

    def test_busy_bad_setting_and_stopped_have_their_own_plain_messages(self):
        busy, setting, stopped = self.notice(3), self.notice(2), self.notice(130)
        self.assertIn("Another WordLyrics run", busy)
        self.assertIn("backup folder", setting)
        self.assertIn("Stopped by you", stopped)
        for text in (busy, setting, stopped):
            self.assertNotIn("read its report", text)
        self.assertIn("read its report", self.notice(4))     # other statuses keep the generic warning


class OneOffRepairsKeepTheChosenFolders(unittest.TestCase):
    def fixture(self, d):
        a, b = Path(d) / "A", Path(d) / "B"
        for folder in (a, b):
            folder.mkdir()
        (a / "one.mp3").write_bytes(b"x")
        return a, b

    def saved(self, m):
        return json.loads((m.home / "menu-settings.json").read_text())["libraries"]

    def test_repair_of_one_song_does_not_replace_the_saved_folder_list(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = self.fixture(d)
            m = make(d, ["0"], folders_saved=[a, b])
            inside = []
            m.song_actions = lambda songs: inside.append(list(m.selected_roots))
            with patch.object(menu.folders, "discover", return_value=([], [])), \
                    patch.object(L, "read_song", return_value=L.Song(str(a / "one.mp3"), "one.mp3")):
                m.repair_path(str(a / "one.mp3"))
            self.assertEqual(inside, [[str(a)]])
            self.assertEqual(m.selected_roots, [str(a), str(b)])
            m.settings()                                      # opening Settings and going Back saves
            self.assertEqual(self.saved(m), [str(a), str(b)])

    def test_resume_does_not_replace_the_saved_folder_list(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = self.fixture(d)
            seen = []
            m = make(d, [], folders_saved=[a, b])
            m.read = lambda _: seen.append(list(m.selected_roots)) or "0"
            row = {"path": str(b / "x.mp3"), "reason": "later", "status": "failed"}
            with patch.object(resume, "pending", return_value=({"source": str(b)}, [row])):
                m.resume_job("saved job")
            self.assertEqual(seen[0], [str(b)])
            self.assertEqual(m.selected_roots, [str(a), str(b)])
            m.save()
            self.assertEqual(self.saved(m), [str(a), str(b)])

    def test_the_users_own_folder_choice_still_replaces_the_list(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = self.fixture(d)
            m = make(d, ["2"], folders_saved=[a, b])
            with patch.object(menu.folders, "discover", return_value=([str(a), str(b)], [])):
                m.choose_folders()
            self.assertEqual(self.saved(m), [str(a)])


class PermissionHelper(unittest.TestCase):
    def test_a_failing_whoami_is_a_plain_error_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "music"
            source.mkdir()
            failing = subprocess.CalledProcessError(1, "whoami")
            with patch("wordlyrics.permissions.subprocess.check_output", side_effect=failing):
                with self.assertRaisesRegex(ValueError, "CURRENT_USER_UNKNOWN"):
                    permissions.repair(str(Path(d) / "app"), str(source), [str(source)], launch=Mock())


if __name__ == "__main__":
    unittest.main()
