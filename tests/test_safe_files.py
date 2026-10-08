"""New lyric files must be complete, exclusive, and isolated from interrupted writes."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wordlyrics import files


class SafeFileTests(unittest.TestCase):
    def test_complete_file_is_published_and_staging_is_removed(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/"example.elrc"
            data="[00:01.00]آزمایش\r\n".encode("utf-8")
            self.assertTrue(files.create_new(target,data))
            self.assertEqual(target.read_bytes(),data)
            self.assertEqual([p.name for p in Path(d).iterdir()],[target.name])

    def test_existing_file_is_never_overwritten_or_staged(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/"example.elrc"
            target.write_bytes(b"original")
            with patch.object(files.tempfile,"mkstemp") as staging:
                self.assertFalse(files.create_new(target,b"replacement"))
                staging.assert_not_called()
            self.assertEqual(target.read_bytes(),b"original")

    def test_interrupted_write_never_leaves_a_partial_lyric_file(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/"example.elrc"
            unrelated=Path(d)/"other-program.tmp"
            unrelated.write_bytes(b"keep me")
            with patch.object(files.os,"fsync",side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    files.create_new(target,b"partial")
            self.assertFalse(target.exists())
            self.assertEqual(unrelated.read_bytes(),b"keep me")
            self.assertEqual([p.name for p in Path(d).iterdir()],[unrelated.name])

    def test_changed_staging_bytes_are_not_published(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/"example.elrc"
            def changed(fd):
                os.write(fd,b"unexpected bytes")
            with patch.object(files.os,"fsync",side_effect=changed):
                with self.assertRaises(OSError):
                    files.create_new(target,b"expected")
            self.assertFalse(target.exists())
            self.assertFalse(list(Path(d).iterdir()))

    def test_a_racing_writer_keeps_its_file(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/"example.elrc"
            publish=files._publish
            def race(staging,path):
                with open(path,"xb") as f:
                    f.write(b"other writer")
                publish(staging,path)
            with patch.object(files,"_publish",side_effect=race):
                self.assertFalse(files.create_new(target,b"our file"))
            self.assertEqual(target.read_bytes(),b"other writer")
            self.assertEqual([p.name for p in Path(d).iterdir()],[target.name])

    def test_posix_publication_is_exclusive_too(self):
        simulated=SimpleNamespace(name="posix",path=os.path,fspath=os.fspath,lstat=os.lstat,fdopen=os.fdopen,fstat=os.fstat,
                                  fsync=os.fsync,link=os.link,unlink=os.unlink)
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/"example.elrc"
            with patch.object(files,"os",simulated):
                self.assertTrue(files.create_new(target,b"complete"))
                self.assertFalse(files.create_new(target,b"replacement"))
            self.assertEqual(target.read_bytes(),b"complete")
            self.assertEqual([p.name for p in Path(d).iterdir()],[target.name])


if __name__=="__main__":
    unittest.main()
