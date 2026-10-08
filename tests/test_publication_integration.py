"""Exercise the app's actual publication/logging path using synthetic timing output."""
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wordlyrics import fetch as F,library as L
from wordlyrics.pipeline import Run,file_bytes


class PublicationIntegrationTests(unittest.TestCase):
    def make_run(self,directory):
        run=object.__new__(Run)
        run.opts=SimpleNamespace(no_lrc=True)
        run.run_dir=str(directory/"run")
        run.lib_home=str(directory/"history")
        Path(run.run_dir).mkdir()
        Path(run.lib_home).mkdir()
        run.write_lock=threading.Lock()
        run.written=[]
        run.log=None
        run.counts={}
        run.screen=SimpleNamespace(tally="")
        return run

    def test_chosen_source_publishes_complete_lyrics_and_audit_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            audio=directory/"example.flac"
            audio.write_bytes(b"untouched audio")
            run=self.make_run(directory)
            found=F.Found(L.PLAIN,"alpha beta",provider="genius.com",url="https://genius.com/Example-song-lyrics",pinned=True)
            s=L.Song(path=str(audio),rel=audio.name,found=found)
            text="[00:01.00]<00:01.00>alpha<00:01.50> <00:01.50>beta<00:02.00>"
            result={"timed":{"lyrics":"found","mode":"plain","status":"timed","elrc":text},"last":None,"keep_lines":None}
            run._apply(s,result)
            self.assertEqual(audio.read_bytes(),b"untouched audio")
            self.assertEqual(audio.with_suffix(".elrc").read_bytes(),file_bytes(text))
            self.assertEqual(s.result["lyrics_from"],"genius.com (chosen page)")
            self.assertEqual(len(run.written),1)
            self.assertTrue((Path(run.run_dir)/"files written.csv").exists())
            self.assertTrue((Path(run.lib_home)/"all files written.csv").exists())
            self.assertFalse(list(directory.glob(".wordlyrics-*.tmp")))

    def test_existing_lyric_is_not_claimed_or_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            audio=directory/"example.flac"
            audio.write_bytes(b"audio")
            lyric=audio.with_suffix(".elrc")
            lyric.write_bytes(b"original lyric file")
            run=self.make_run(directory)
            s=L.Song(path=str(audio),rel=audio.name,found=F.Found(L.PLAIN,"alpha beta"))
            result={"timed":{"lyrics":"found","mode":"plain","status":"timed","elrc":"new output"},"last":None,"keep_lines":None}
            run._apply(s,result)
            self.assertEqual(lyric.read_bytes(),b"original lyric file")
            self.assertEqual(s.result["status"],"skipped")
            self.assertFalse(run.written)


if __name__=="__main__":
    unittest.main()
