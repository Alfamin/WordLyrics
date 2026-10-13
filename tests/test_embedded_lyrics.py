"""Embedded lyrics already displayed by Noctis must not be treated as missing."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave
from mutagen.id3 import SYLT, TXXX, USLT
from mutagen.wave import WAVE

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wordlyrics import library as L

ELRC = "[00:00.100]<00:00.100>alpha <00:00.300>beta"


class EmbeddedLyricsTests(unittest.TestCase):
    def test_real_id3_alias_is_detected_and_preserved_without_retiming(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "Example - Synthetic.wav"
            with wave.open(str(path), "wb") as f:
                f.setnchannels(1); f.setsampwidth(2); f.setframerate(8000)
                f.writeframes(b"\0\0" * 8000)
            tags = WAVE(path); tags.add_tags()
            tags.tags.add(TXXX(encoding=3, desc="SYNCEDLYRICS", text=[ELRC])); tags.save()
            original = path.read_bytes()
            song = L.read_song(str(path), path.name, path.stat().st_size)
            self.assertEqual(song.tier, L.WORD)
            self.assertEqual(song.skip, "already has word-by-word lyrics")
            self.assertEqual(song.text, ELRC)
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(path.with_suffix(".elrc").exists())

    def test_every_id3_text_alias_and_case_spelling(self):
        from mutagen.id3 import ID3
        for alias in ("LYRICS", "UNSYNCEDLYRICS", "UNSYNCED LYRICS", "SYNCEDLYRICS", "sYnCeD LyRiCs"):
            tags = ID3(); tags.add(TXXX(desc=alias, text=[ELRC]))
            with self.subTest(alias=alias), patch("mutagen.File", return_value=SimpleNamespace(tags=tags, info=SimpleNamespace(length=1))):
                self.assertIn(ELRC, L.embedded("unused.wav")[1])

    def test_unrelated_txxx_does_not_become_lyrics(self):
        from mutagen.id3 import ID3
        tags = ID3(); tags.add(TXXX(desc="COMMENT", text=[ELRC]))
        with patch("mutagen.File", return_value=SimpleNamespace(tags=tags, info=SimpleNamespace(length=1))):
            self.assertEqual(L.embedded("unused.wav")[1], [])

    def test_vorbis_spaced_aliases_are_detected(self):
        for alias in ("UNSYNCED LYRICS", "SYNCED LYRICS", "SYNCEDLYRICS"):
            with self.subTest(alias=alias), patch("mutagen.File", return_value=SimpleNamespace(tags={alias:[ELRC]}, info=SimpleNamespace(length=1))):
                self.assertIn(ELRC, L.embedded("unused.flac")[1])

    def test_word_sylt_keeps_lines_words_and_millisecond_stamps(self):
        frame = SYLT(type=1, format=2, text=[("\nalpha ", 123), ("beta", 456), ("\ngamma ", 1001), ("delta", 1456)])
        text = L.sylt_text(frame)
        self.assertEqual(text, "[00:00.123]<00:00.123>alpha <00:00.456>beta\n[00:01.001]<00:01.001>gamma <00:01.456>delta")
        self.assertEqual(L.classify(text), L.WORD)

    def test_single_word_style_line_is_still_word_timed(self):
        frame = SYLT(type=1, format=2, text=[("\nalpha ", 1000), ("beta", 1400)])
        self.assertEqual(L.classify(L.sylt_text(frame)), L.WORD)

    def test_line_style_remains_line_timed(self):
        for entries in ([('alpha beta',1000),('gamma delta',2000)], [('\nalpha beta',1000),('\ngamma delta',2000)]):
            self.assertEqual(L.classify(L.sylt_text(SYLT(type=1, format=2, text=entries))), L.LINE)

    def test_frame_count_timing_is_not_invented(self):
        self.assertEqual(L.classify(L.sylt_text(SYLT(type=1, format=1, text=[("alpha", 25), ("beta", 50)]))), L.PLAIN)

    def test_non_lyric_sylt_is_ignored(self):
        self.assertEqual(L.sylt_text(SYLT(type=2, format=2, text=[("announcement", 1000)])), "")

    def test_equal_timestamps_keep_syllable_order(self):
        frame = SYLT(type=1, format=2, text=[("\nalpha", 1000), ("beta",1000)])
        self.assertIn("<00:01.000>alpha<00:01.000>beta", L.sylt_text(frame))


if __name__ == "__main__":
    unittest.main()
