"""Explicit sources follow exact recordings; timing never publishes at an obsolete path."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wordlyrics import library as L, source_choices as S
from wordlyrics.pipeline import Run

URL = "https://genius.com/Example-song-lyrics"


class MoveRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.home = self.root / "app"
        self.audio = self.root / "Original.flac"; self.audio.write_bytes(b"exact recording bytes")
        self.pin = self.audio.with_suffix(".lyrics-source.txt"); self.pin.write_text(URL)
        S.remember(self.audio, self.pin, self.home)

    def test_rename_recovers_source_without_copying_or_deleting_music_files(self):
        renamed = self.root / "Renamed.flac"; self.audio.rename(renamed)
        before = {p.name:p.read_bytes() for p in self.root.glob('*') if p.is_file()}
        with patch.object(L,"embedded",return_value=({"seconds":100},[])):
            song = L.read_song(str(renamed),renamed.name,renamed.stat().st_size,source_home=self.home)
        self.assertEqual(song.lyrics_url, URL)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.glob('*') if p.is_file()})

    def test_exact_copy_uses_digest_fallback(self):
        copied = self.root / "Copy.flac"; shutil.copyfile(self.audio,copied)
        self.assertEqual(S.recover(copied,self.home),(URL,""))

    def test_same_name_and_size_do_not_match_other_recording(self):
        other = self.root / "Other.flac"; other.write_bytes(b"wrong recording bytes")
        self.assertEqual(other.stat().st_size,self.audio.stat().st_size)
        self.assertEqual(S.recover(other,self.home),("",""))

    def test_deleting_or_editing_original_pin_revokes_recovery(self):
        self.pin.write_text("auto")
        self.assertEqual(S.recover(self.audio,self.home),("",""))
        self.pin.unlink()
        self.assertEqual(S.recover(self.audio,self.home),("",""))

    def test_current_pin_wins_over_remembered_source(self):
        moved=self.root/"Moved.flac";shutil.copyfile(self.audio,moved)
        moved.with_suffix(".lyrics-source.txt").write_text("auto")
        with patch.object(L,"embedded",return_value=({"seconds":100},[])):
            song=L.read_song(str(moved),moved.name,moved.stat().st_size,source_home=self.home)
        self.assertFalse(song.lyrics_url)

    def test_conflicting_sources_are_not_guessed(self):
        second=self.root/"Second.flac";shutil.copyfile(self.audio,second)
        pin=second.with_suffix(".lyrics-source.txt");pin.write_text("https://genius.com/Other-song-lyrics")
        S.remember(second,pin,self.home)
        third=self.root/"Third.flac";shutil.copyfile(self.audio,third)
        chosen,reason=S.recover(third,self.home)
        self.assertFalse(chosen);self.assertIn("SOURCE_RECOVERY_CONFLICT",reason)

    def test_bad_private_record_is_preserved_skipped_and_reported(self):
        record=next((self.home/"source choices").glob('*.json'));record.write_text("invalid")
        S.SKIPPED.clear()
        self.assertEqual(S.recover(self.audio,self.home),("",""))
        self.assertEqual(list(S.SKIPPED),[record.name]);self.assertEqual(record.read_text(),"invalid")

    def test_one_damaged_record_does_not_fail_every_other_song(self):
        copied=self.root/"Copy.flac";shutil.copyfile(self.audio,copied)
        second=self.root/"Second.flac";second.write_bytes(b"second recording")
        pin=second.with_suffix(".lyrics-source.txt");pin.write_text("https://genius.com/Other-song-lyrics")
        S.remember(second,pin,self.home)
        (self.home/"source choices"/"too-big.json").write_text(" "*9000)
        pin.write_text("not a genius page")              # its record now points at an invalid choice
        unrelated=self.root/"Unrelated.flac";unrelated.write_bytes(b"nothing saved for this one")
        S.SKIPPED.clear()
        self.assertEqual(S.recover(copied,self.home),(URL,""))      # the good record still works
        self.assertEqual(S.recover(unrelated,self.home),("",""))    # a lyric-less song is not blocked
        self.assertEqual(len(S.SKIPPED),2)
        reason=S.recover(second,self.home)[1]                        # ... but a song's OWN damaged choice is still reported
        self.assertIn("SOURCE_RECOVERY_INVALID",reason)

    def test_invalid_url_and_linked_pins_are_not_remembered(self):
        self.pin.write_text("https://evil.example/song-lyrics")
        with self.assertRaises(OSError):S.remember(self.audio,self.pin,self.home)
        saved=self.root/"saved.txt";saved.write_text(URL);self.pin.unlink();os.link(saved,self.pin)
        with self.assertRaises(OSError):S.remember(self.audio,self.pin,self.home)

    def test_failed_private_save_keeps_previous_record_and_removes_only_own_temporary(self):
        record=next((self.home/"source choices").glob('*.json'));before=record.read_bytes()
        unrelated=record.parent/"other.tmp";unrelated.write_bytes(b"keep")
        self.audio.write_bytes(b"updated recording bytes")
        with patch.object(S.os,"replace",side_effect=OSError("disk full")):
            with self.assertRaises(OSError):S.remember(self.audio,self.pin,self.home)
        self.assertEqual(record.read_bytes(),before);self.assertEqual(unrelated.read_bytes(),b"keep")
        self.assertFalse(list(record.parent.glob('.source-*.tmp')))

    def test_move_during_timing_never_creates_or_replaces_lyrics(self):
        with patch.object(L,"embedded",return_value=({"seconds":100},[])):
            song=L.read_song(str(self.audio),self.audio.name,self.audio.stat().st_size,ignore_source=True)
        runner=Mock();runner.opts=Mock(redo=False,repair_lyrics=False);runner._put_file=Mock()
        renamed=self.root/"Renamed.flac";self.audio.rename(renamed)
        with self.assertRaisesRegex(OSError,"AUDIO_MOVED"):Run.put_file(runner,song,'.elrc','synthetic words','word')
        runner._put_file.assert_not_called()
        self.assertFalse(self.audio.with_suffix('.elrc').exists())

    def test_different_file_at_old_path_is_preserved(self):
        with patch.object(L,"embedded",return_value=({"seconds":100},[])):
            song=L.read_song(str(self.audio),self.audio.name,self.audio.stat().st_size,ignore_source=True)
        self.audio.rename(self.root/"Renamed.flac");self.audio.write_bytes(b"another recording")
        runner=Mock();runner.opts=Mock(redo=False,repair_lyrics=False)
        with self.assertRaisesRegex(OSError,"AUDIO_CHANGED"):Run.put_file(runner,song,'.elrc','synthetic words','word')
        runner._put_file.assert_not_called();self.assertEqual(self.audio.read_bytes(),b"another recording")


if __name__ == '__main__':unittest.main()
