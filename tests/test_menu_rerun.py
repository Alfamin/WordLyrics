"""Exercise cancellation, exact song scope, publication and recovery without real music/models."""
from contextlib import redirect_stdout
import csv
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from wordlyrics import __main__ as cli,backup,files,library as L,menu,report,rerun,undo
from wordlyrics.pipeline import Run,library_home,file_bytes

OLD="[00:05.00]<00:05.00>alpha<00:05.50> <00:05.50>beta<00:06.00>\n"
NEW="[00:01.00]<00:01.00>alpha<00:01.50> <00:01.50>beta<00:02.00>\n"


class MenuTests(unittest.TestCase):
    def make_menu(self,home,root,replies):
        responses=iter(replies)
        m=menu.Menu(home,[str(root)],Mock(return_value=0),reader=lambda _:next(responses),writer=Mock())
        return m

    def song(self,root,name="one.flac",**kw):
        return L.Song(path=str(root/name),rel=name,seconds=100,**kw)

    def test_numbers_ranges_and_other_keyboard_layouts(self):
        self.assertEqual(menu.selection("۱،۳-۵ 3",5),[0,2,3,4])
        self.assertEqual(menu.selection("0",0),[])
        for bad in ("0,1","2-1","1-9","-1","1+2","__import__('os')"):
            with self.subTest(bad=bad),self.assertRaises(ValueError):menu.selection(bad,5)

    def test_multiple_dropped_paths_with_spaces(self):
        with tempfile.TemporaryDirectory() as d:
            a=Path(d)/"one song.flac";b=Path(d)/"two song.mp3"
            a.write_bytes(b"a");b.write_bytes(b"b")
            self.assertEqual(menu.dropped_files('"%s" "%s" "%s"'%(a,b,a)),[str(a),str(b)])
            self.assertEqual(menu.dropped_files(str(a)),[str(a)])
            with self.assertRaises(ValueError):menu.dropped_files('"%s"'%d)

    def test_cancel_is_no_execution_or_selection_file(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"music";root.mkdir();home=Path(d)/"app"
            m=self.make_menu(home,root,["1","0"])
            m.job([self.song(root)],redo=True)
            m.execute.assert_not_called()
            self.assertFalse(home.exists())

    def test_full_library_has_second_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"music";root.mkdir();home=Path(d)/"app"
            m=self.make_menu(home,root,["1","1","0"])
            m.job([self.song(root)],redo=True,whole=True)
            m.execute.assert_not_called()
            m=self.make_menu(home,root,["3","1","1"])
            m.job([self.song(root)],redo=True,whole=True)
            args=m.execute.call_args.args[0]
            self.assertIn("--confirm-redo",args)
            self.assertEqual(args[args.index("--retime-mode")+1],"fresh")

    def test_selected_nested_songs_keep_library_history_and_exact_scope(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"music";root.mkdir();home=Path(d)/"app"
            m=self.make_menu(home,root,["1","1"])
            songs=[self.song(root,"album/one.flac"),self.song(root,"album/two.flac")]
            m.job(songs,redo=True)
            args=m.execute.call_args.args[0]
            self.assertEqual(args[0],str(root))
            listed=Path(args[args.index("--songs-from")+1]).read_text().splitlines()
            self.assertEqual(listed,[s.path for s in songs])
            self.assertIn("--yes",args)
            self.assertIn("--no-open",args)
            self.assertNotIn("--offline",args)

    def test_successful_word_timed_songs_do_not_show_as_missing(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"music";root.mkdir()
            m=self.make_menu(Path(d)/"app",root,["3","0"])
            old=self.song(root,"old.flac",tier=L.PLAIN,previous_tier=L.WORD)
            missing=self.song(root,"missing.flac",tier=L.NONE,previous_tier=L.NONE)
            m.scan=Mock(return_value=[old,missing]);m.history=Mock(return_value={})
            m.choose=Mock(return_value=[])
            self.assertEqual(m.run(),0)
            self.assertEqual(m.choose.call_args.args[0],[missing])

    def test_page_navigation_uses_stable_visible_indices(self):
        with tempfile.TemporaryDirectory() as d:
            m=self.make_menu(Path(d)/"app",Path(d),["2","1","26,30"])
            songs=[self.song(Path(d),"%02d.flac"%i) for i in range(30)]
            self.assertEqual(m.choose(songs),[songs[25],songs[29]])

    def test_eof_exits_menu(self):
        with tempfile.TemporaryDirectory() as d:
            m=menu.Menu(d,[],Mock(),reader=Mock(side_effect=EOFError),writer=Mock())
            self.assertEqual(m.run(),0);m.execute.assert_not_called()

    def test_drive_root_cannot_be_selected_as_library(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).anchor
            m=self.make_menu(Path(d)/"app",Path(root),[root])
            self.assertIsNone(m.root())

    def test_chosen_lyrics_can_recover_an_invalid_source_pin(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"music";root.mkdir();audio=root/"one.flac";audio.write_bytes(b"audio")
            audio.with_suffix(".lyrics-source.txt").write_text("invalid source")
            text=Path(d)/"lyrics.txt";text.write_text("alpha beta")
            with patch.object(L,"embedded",return_value=({"seconds":100},[])):
                s=L.read_song(str(audio),audio.name,5,redo=True)
                self.assertTrue(s.skip)
                m=self.make_menu(Path(d)/"app",root,["1"])
                m.job([s],redo=True,lyrics_file=str(text),mode_hint="current")
            self.assertIn("--lyrics-file",m.execute.call_args.args[0])

    def test_source_update_and_reset_have_independent_backups(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"music";root.mkdir();home=Path(d)/"app"
            audio=root/"one.flac";audio.write_bytes(b"audio")
            pin=audio.with_suffix(".lyrics-source.txt");pin.write_text("https://genius.com/Old-song-lyrics")
            s=self.song(root)
            m=self.make_menu(home,root,["2","4","0"])
            m.scan=Mock(return_value=[s]);m.history=Mock(return_value={});m.choose=Mock(return_value=[s])
            m.source_choice()
            self.assertEqual(pin.read_text().strip(),"auto")
            saved=list((home/"source-choice backups").glob("*/*.txt"))
            self.assertEqual(len(saved),1)
            self.assertEqual(saved[0].read_text(),"https://genius.com/Old-song-lyrics")
            m.execute.assert_not_called()


class RetimeTests(unittest.TestCase):
    def fixture(self,d):
        root=Path(d)/"music";root.mkdir()
        home=Path(d)/"app";home.mkdir()
        audio=root/"one.flac";audio.write_bytes(b"untouched audio")
        lyric=audio.with_suffix(".elrc");lyric.write_bytes(file_bytes(OLD))
        return root,home,audio,lyric

    def run_fake(self,root,home,audio,accept=True,mode="current",offline=False):
        opts=cli.parser().parse_args([str(root),"--yes","--redo","--retime-mode",mode,"--confirm-redo",
                                     "--backup-to",str(root.parent/"backups"),"--no-lrc","--no-open"])
        opts.songs=[str(audio)];opts.speed="full";opts.offline=offline
        fetched=Mock()
        with patch("wordlyrics.pipeline.Screen",return_value=Mock()),patch("wordlyrics.pipeline.decide.low_priority"), \
             patch.object(L,"embedded",return_value=({"seconds":100,"artist":"Artist","title":"Song"},[])), \
             patch("wordlyrics.pipeline.fetch.lookup",fetched):
            run=Run(str(root),opts,str(home))
            run.get_models=Mock(return_value="unused")
            run.start_engine=Mock(return_value=SimpleNamespace(is_alive=lambda:False))
            def listen(stage,ready,total):
                while not ready.empty():
                    s=ready.get_nowait()
                    if s is None:continue
                    t={"lyrics":"yours","mode":"plain","status":"timed" if accept else "not_timed"}
                    if accept:t["elrc"]=NEW
                    else:t["reason"]="insufficient confidence"
                    run._apply(s,{"timed":t if accept else None,"last":t,"keep_lines":None})
            run.listen=listen
            run.run()
        return run,fetched

    def test_rerun_has_real_verified_backup_publication_journal_and_restore(self):
        with tempfile.TemporaryDirectory() as d:
            root,home,audio,lyric=self.fixture(d)
            original=lyric.read_bytes()
            run,fetched=self.run_fake(root,home,audio)
            fetched.assert_not_called()
            self.assertEqual(audio.read_bytes(),b"untouched audio")
            self.assertEqual(lyric.read_bytes(),file_bytes(NEW))
            self.assertTrue(run.backup_rec["verified_byte_for_byte"])
            self.assertTrue(run.check["clean"])
            self.assertEqual(run.check["retimed"],[lyric.name])
            rows=rerun.records(str(home),str(root));self.assertEqual(len(rows),1)
            self.assertEqual(Path(rows[0]["backup"]).read_bytes(),original)
            self.assertEqual(Path(rows[0]["candidate"]).read_bytes(),file_bytes(NEW))
            _,move,keep=undo.plan(str(home),str(root));self.assertFalse(move);self.assertTrue(keep)
            report_path=report.write(run)
            self.assertIn("explicitly retimed",Path(report_path).read_text())
            with open(Path(run.run_dir)/"songs.csv",encoding="utf-8-sig",newline="") as f:
                self.assertEqual(next(csv.DictReader(f))["lyrics before"],"word-timed")
            rerun.restore(str(home),str(root),rows[0])
            self.assertEqual(lyric.read_bytes(),original)
            self.assertEqual(audio.read_bytes(),b"untouched audio")
            self.assertEqual(len(list(Path(run.lib_home).glob("restores/*/*.elrc"))),1)

    def test_failure_keeps_old_timing_and_has_no_replacement_record(self):
        with tempfile.TemporaryDirectory() as d:
            root,home,audio,lyric=self.fixture(d);original=lyric.read_bytes()
            run,_=self.run_fake(root,home,audio,accept=False)
            self.assertEqual(lyric.read_bytes(),original)
            self.assertFalse(run.written)
            self.assertFalse(rerun.records(str(home),str(root)))

    def test_fresh_offline_cannot_reuse_old_words_or_replace(self):
        with tempfile.TemporaryDirectory() as d:
            root,home,audio,lyric=self.fixture(d);original=lyric.read_bytes()
            run,_=self.run_fake(root,home,audio,mode="fresh",offline=True)
            self.assertEqual(lyric.read_bytes(),original)
            self.assertEqual(run.songs[0].result["status"],"no_lyrics")
            run.start_engine.assert_not_called()

    def test_restore_refuses_changed_current_or_changed_backup(self):
        with tempfile.TemporaryDirectory() as d:
            root,home,audio,lyric=self.fixture(d)
            run,_=self.run_fake(root,home,audio);row=rerun.records(str(home),str(root))[0]
            lyric.write_bytes(b"user edit")
            with self.assertRaises(OSError):rerun.restore(str(home),str(root),row)
            self.assertEqual(lyric.read_bytes(),b"user edit")
            lyric.write_bytes(file_bytes(NEW));Path(row["backup"]).write_bytes(b"damaged backup")
            with self.assertRaises(OSError):rerun.restore(str(home),str(root),row)
            self.assertEqual(lyric.read_bytes(),file_bytes(NEW))

    def test_replacement_refuses_unverified_changed_or_wrong_backup(self):
        with tempfile.TemporaryDirectory() as d:
            root,home,audio,lyric=self.fixture(d);original=lyric.read_bytes()
            saved=Path(d)/"backup.elrc";saved.write_bytes(original)
            expected=hashlib.sha256(original).hexdigest()
            lyric.write_bytes(b"a user edit")
            with self.assertRaises(OSError):files.replace_backed_up(lyric,b"new",saved,expected)
            self.assertEqual(lyric.read_bytes(),b"a user edit")
            lyric.write_bytes(original);saved.write_bytes(b"bad backup")
            with self.assertRaises(OSError):files.replace_backed_up(lyric,b"new",saved,expected)
            self.assertEqual(lyric.read_bytes(),original)
            with self.assertRaises(OSError):files.replace_backed_up(audio,b"new",saved,expected)
            self.assertEqual(audio.read_bytes(),b"untouched audio")

    def test_guarded_guided_current_and_fresh_reading(self):
        with tempfile.TemporaryDirectory() as d:
            root,home,audio,lyric=self.fixture(d)
            with patch.object(L,"embedded",return_value=({"seconds":100},[])):
                ordinary=L.read_song(str(audio),audio.name,5)
                current=L.read_song(str(audio),audio.name,5,redo=True)
                guided=L.read_song(str(audio),audio.name,5,redo=True,retime_mode="guided")
                unanchored=L.read_song(str(audio),audio.name,5,redo=True,retime_mode="unanchored")
                fresh=L.read_song(str(audio),audio.name,5,redo=True,retime_mode="fresh")
            self.assertTrue(ordinary.skip)
            self.assertEqual(current.text,"[00:05.000]alpha beta")
            self.assertEqual(current.tier,L.LINE)
            self.assertEqual(unanchored.text,"alpha beta")
            self.assertEqual(unanchored.tier,L.PLAIN)
            self.assertEqual(guided.tier,L.LINE)
            self.assertEqual(fresh.tier,L.NONE);self.assertEqual(fresh.text,"")
            self.assertEqual(fresh.previous_tier,L.WORD)

    def test_malformed_or_traversing_restore_records_are_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            root,home,audio,lyric=self.fixture(d)
            folder=Path(library_home(str(home),str(root)))/"run x";folder.mkdir(parents=True)
            rows=[[],{},dict(rel="../evil.elrc",backup="x",sha256="0"*64,previous_sha256="0"*64)]
            (folder/"retimed.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
            self.assertFalse(rerun.records(str(home),str(root)))


class CliMenuTests(unittest.TestCase):
    def test_default_tty_opens_menu_and_yes_never_opens_menu(self):
        with tempfile.TemporaryDirectory() as d,redirect_stdout(StringIO()), \
             patch.object(cli.sys.stdin,"isatty",return_value=True),patch.object(cli.sys.stdout,"isatty",return_value=True), \
             patch("wordlyrics.menu.run_menu",return_value=0) as entry,patch.object(cli,"already_running",return_value=True):
            self.assertEqual(cli.main([d]),0);entry.assert_called_once()
            entry.reset_mock()
            self.assertEqual(cli.main([d,"--yes"]),3);entry.assert_not_called()

    def test_unconfirmed_whole_redo_is_rejected_before_any_run(self):
        with tempfile.TemporaryDirectory() as d,redirect_stdout(StringIO()),patch("wordlyrics.pipeline.Run") as run:
            self.assertEqual(cli.main([d,"--yes","--redo"]),2);run.assert_not_called()

    def test_every_job_releases_the_gpu_guard_on_failure(self):
        with patch.object(cli,"_main",side_effect=OSError),patch.object(cli,"release_running") as release:
            with self.assertRaises(OSError):cli.main(["unused"])
            release.assert_called_once()


if __name__=="__main__":unittest.main()
