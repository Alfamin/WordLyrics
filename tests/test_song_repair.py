from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from wordlyrics import __main__ as cli,drafts,fetch,firststart,library as L,menu,repair,resume,search
from wordlyrics.pipeline import Run,file_bytes

OLD='[00:01.00]<00:01.00>old<00:02.00> <00:02.00>words<00:03.00>\n'
NEW='[00:02.00]<00:02.00>new<00:03.00> <00:03.00>words<00:04.00>\n'


class SearchAndEdit(unittest.TestCase):
    def songs(self):
        return [L.Song("/music/01.mp3","album/01.mp3",artist="Kanye West",title="Runaway",album="Fantasy"),
                L.Song("/music/02.mp3","album/02.mp3",artist="Other",title="Runaway (Live)"),
                L.Song("/music/03.mp3","Only File Name.mp3",artist="Other",title="Different")]

    def test_partial_metadata_filename_unicode_and_typos(self):
        songs=self.songs()
        self.assertEqual(search.find(songs,"run")[0],songs[:2])
        self.assertEqual(search.find(songs,"kanye run")[0],[songs[0]])
        self.assertEqual(search.find(songs,"fantasy")[0],[songs[0]])
        self.assertEqual(search.find(songs,"file name")[0],[songs[2]])
        self.assertEqual(search.find(songs,"runawy")[0],songs[:2]);self.assertTrue(search.find(songs,"runawy")[1])
        self.assertFalse(search.find(songs,"totally unrelated")[0])
        s=L.Song("/music/p.mp3","p.mp3",title="Café فارسی")
        self.assertEqual(search.find([s],"CAFÉ")[0],[s]);self.assertEqual(search.find([s],"فارسی")[0],[s])

    def test_numbered_search_selects_match_without_exact_name(self):
        with tempfile.TemporaryDirectory() as d:
            answers=iter(["run","۲"])
            m=menu.Menu(d,[],Mock(),reader=lambda _:next(answers),writer=Mock())
            selected=m.search_songs(self.songs())
            self.assertEqual(selected[0].artist,"Other")

    def test_search_no_matches_can_retry_and_eof_cancels(self):
        with tempfile.TemporaryDirectory() as d:
            answers=iter(["absent","run","1"])
            m=menu.Menu(d,[],Mock(),reader=lambda _:next(answers),writer=Mock())
            self.assertEqual(m.search_songs(self.songs())[0].artist,"Kanye West")
            m.read=Mock(side_effect=EOFError)
            self.assertEqual(m.search_songs(self.songs()),[])

    def test_draft_strips_timestamps_and_keeps_unicode_without_touching_original(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);audio=root/"one.mp3";audio.write_bytes(b"audio")
            audio.with_suffix(".elrc").write_text(OLD)
            song=L.Song(str(audio),audio.name,size=5)
            with patch.object(L,"embedded",return_value=({"seconds":100},[])):
                path=drafts.create(str(root/"app"),song)
            self.assertEqual(Path(path).read_text(),"old words\n")
            self.assertEqual(audio.with_suffix(".elrc").read_text(),OLD)
            path=drafts.create(str(root/"app"),song,"English\nفارسی")
            self.assertEqual(Path(path).read_text(encoding="utf-8"),"English\nفارسی\n")

    def test_editor_uses_arguments_without_a_shell(self):
        with patch.object(drafts.os,"name","nt"),patch.object(drafts.subprocess,"Popen") as launch:
            drafts.open_editor(r"C:\music\%TEMP% & فارسی.txt")
            self.assertFalse(launch.call_args.kwargs["shell"])
            self.assertEqual(launch.call_args.args[0][-1],r"C:\music\%TEMP% & فارسی.txt")

    def test_custom_paste_runs_exact_song_with_backup_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);audio=root/"one.mp3";audio.write_bytes(b"audio")
            answers=iter(["2","alpha beta", "فارسی", ".done", "1"])
            executor=Mock(return_value=4)
            m=menu.Menu(str(root/"app"),[str(root)],executor,reader=lambda _:next(answers),writer=Mock())
            with patch.object(L,"embedded",return_value=({"seconds":100},[])):
                m.custom_lyrics(L.Song(str(audio),audio.name,size=5))
            args=executor.call_args.args[0]
            self.assertIn("--repair-lyrics",args);self.assertIn("--lyrics-file",args)
            self.assertIn("--offline",args)  # corrected/custom words do not fetch another provider behind the user's choice
            self.assertNotIn("--ignore-lyric-source",args)
            self.assertEqual(Path(args[args.index("--songs-from")+1]).read_text().splitlines(),[str(audio)])
            self.assertEqual(Path(args[args.index("--lyrics-file")+1]).read_text(encoding="utf-8"),"alpha beta\nفارسی\n")

    def test_lrclib_choices_filter_instrumentals_empty_and_censored(self):
        with tempfile.TemporaryDirectory() as d:
            row={"id":7,"artistName":"Artist","trackName":"Song","duration":100,"plainLyrics":"alpha beta gamma "*10}
            rows=[row,dict(row,id=8,plainLyrics="",instrumental=True),dict(row,id=9,plainLyrics="f*** "*40)]
            client=Mock();client.get.return_value=("ok",rows)
            song=L.Song("one.mp3","one.mp3",artist="Artist",title="Song",seconds=100,explicit=True)
            with patch("wordlyrics.fetch.Client",return_value=client):
                self.assertEqual([r["id"] for r in drafts.lrclib_results(d,song,"partial")],[7])
                client.get.return_value=("ok",[dict(row,plainLyrics="f*** "*40,syncedLyrics="\n".join("[00:%02d.00]alpha beta gamma"%i for i in range(1,6)))])
                selected=drafts.lrclib_results(d,song,"partial")
                self.assertNotIn("f***",selected[0]["chosen_text"])
                client.get.assert_called_with("/search",{"q":"partial"})
                client.get.return_value=("error","LRCLIB [DNS_FAILED]: connection failed")
                with self.assertRaisesRegex(OSError,"DNS_FAILED"):drafts.lrclib_results(d,song,"partial")


class GuardedRepair(unittest.TestCase):
    def run_fake(self,d,accept=True,fail_write=False,mutate=False,racing_writer=False,commit_failure=False):
        root=Path(d)/"music";root.mkdir();home=Path(d)/"app";home.mkdir()
        audio=root/"one.flac";audio.write_bytes(b"untouched audio")
        audio.with_suffix(".elrc").write_bytes(file_bytes(OLD))
        audio.with_suffix(".ttml").write_bytes(b"original preferred lyrics")
        audio.with_suffix(".lyricsfile").write_bytes(b"original second preferred lyrics")
        audio.with_suffix(".lyrics-source.txt").write_text("https://genius.com/Old-wrong-lyrics")
        opts=cli.parser().parse_args([str(root),"--yes","--redo","--retime-mode","fresh","--confirm-redo",
                                     "--repair-lyrics","--ignore-lyric-source","--backup-to",str(Path(d)/"backups"),"--no-lrc","--no-open"])
        opts.songs=[str(audio)];opts.speed="full"
        found=fetch.Found(L.PLAIN,"alpha beta gamma "*10)
        with patch("wordlyrics.pipeline.Screen",return_value=Mock()),patch("wordlyrics.pipeline.decide.low_priority"), \
             patch.object(L,"embedded",return_value=({"seconds":100,"artist":"Artist","title":"Song"},[])), \
             patch("wordlyrics.pipeline.fetch.lookup",return_value=(found,"")):
            run=Run(str(root),opts,str(home))
            run.get_models=Mock(return_value="unused");run.start_engine=Mock(return_value=SimpleNamespace(is_alive=lambda:False))
            def listen(stage,ready,total):
                while not ready.empty():
                    s=ready.get_nowait()
                    if s is None:continue
                    if mutate:audio.with_suffix(".ttml").write_bytes(b"user changed preferred lyrics")
                    info={"lyrics":"found","mode":"plain","status":"timed" if accept else "not_timed"}
                    if accept:info["elrc"]=NEW
                    else:info["reason"]="insufficient confidence"
                    res={"timed":info if accept else None,"last":info,"keep_lines":None}
                    if racing_writer:
                        def other_writer(*args,**kwargs):audio.with_suffix(".elrc").write_bytes(file_bytes(NEW));return False
                        with patch.object(run,"_put_file",side_effect=other_writer):run.apply(s,SimpleNamespace(result=lambda:res))
                    elif commit_failure:
                        with patch.object(repair.Publication,"commit",side_effect=OSError("disk full")):run.apply(s,SimpleNamespace(result=lambda:res))
                    elif fail_write:
                        with patch.object(run,"_put_file",side_effect=OSError("disk full")):
                            run.apply(s,SimpleNamespace(result=lambda:res))
                    else:run.apply(s,SimpleNamespace(result=lambda:res))
            run.listen=listen;run.run()
        return run,root,home,audio

    def test_accepted_timing_archives_preferred_files_and_restore_is_guarded(self):
        with tempfile.TemporaryDirectory() as d:
            run,root,home,audio=self.run_fake(d)
            self.assertEqual(audio.read_bytes(),b"untouched audio")
            self.assertEqual(audio.with_suffix(".elrc").read_bytes(),file_bytes(NEW))
            self.assertFalse(audio.with_suffix(".ttml").exists());self.assertFalse(audio.with_suffix(".lyricsfile").exists())
            self.assertEqual(len(list(root.glob("*.wordlyrics-inactive-*.bak"))),2)
            self.assertTrue(run.check["clean"]);self.assertEqual(len(run.check["archived_incorrect_lyrics"]),2)
            rows=repair.records(str(home),str(root));self.assertEqual(len(rows),2)
            self.assertFalse(resume.pending(str(home),run.run_dir)[1])
            repair.restore(str(root),rows[0])
            self.assertTrue((root/rows[0]["rel"]).exists())
            with self.assertRaises(OSError):repair.restore(str(root),rows[0])
            Path(root/rows[1]["inactive_rel"]).write_bytes(b"changed archive")
            with self.assertRaises(OSError):repair.restore(str(root),rows[1])

    def test_failed_alignment_or_publication_keeps_original_preferred_lyrics(self):
        for kwargs in ({"accept":False},{"fail_write":True}):
            with self.subTest(kwargs=kwargs),tempfile.TemporaryDirectory() as d:
                run,root,home,audio=self.run_fake(d,**kwargs)
                self.assertEqual(audio.with_suffix(".elrc").read_bytes(),file_bytes(OLD))
                self.assertEqual(audio.with_suffix(".ttml").read_bytes(),b"original preferred lyrics")
                self.assertFalse(list(root.glob("*.wordlyrics-inactive-*.bak")))
                _,pending=resume.pending(str(home),run.run_dir);self.assertEqual(len(pending),1)
                executor=Mock(return_value=4);resume.submit(str(home),run.run_dir,executor)
                self.assertIn("--repair-lyrics",executor.call_args.args[0]);self.assertIn("--ignore-lyric-source",executor.call_args.args[0])

    def test_user_edit_during_model_run_is_never_archived_or_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            run,root,home,audio=self.run_fake(d,mutate=True)
            self.assertEqual(audio.with_suffix(".ttml").read_bytes(),b"user changed preferred lyrics")
            self.assertEqual(audio.with_suffix(".elrc").read_bytes(),file_bytes(OLD))
            self.assertFalse(list(root.glob("*.wordlyrics-inactive-*.bak")))

    def test_resume_can_switch_failed_custom_words_back_to_fresh_online_lookup(self):
        with tempfile.TemporaryDirectory() as d:
            run,root,home,audio=self.run_fake(d,accept=False)
            request=Path(run.run_dir)/"request.json";data=json.loads(request.read_text(encoding="utf-8"))
            data["options"].update(offline=True,lyrics_file=str(home/"custom.txt"),ignore_lyric_source=False)
            request.write_text(json.dumps(data),encoding="utf-8")
            execute=Mock(return_value=4);resume.submit(str(home),run.run_dir,execute,fresh=True)
            args=execute.call_args.args[0]
            self.assertNotIn("--offline",args);self.assertNotIn("--lyrics-file",args);self.assertIn("--ignore-lyric-source",args)
            self.assertEqual(Path(args[args.index("--songs-from")+1]).read_text().splitlines(),[str(audio)])

    def test_racing_writer_with_identical_output_is_not_removed_by_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            run,root,home,audio=self.run_fake(d,racing_writer=True)
            self.assertEqual(audio.with_suffix(".elrc").read_bytes(),file_bytes(NEW))
            self.assertTrue(audio.with_suffix(".ttml").exists())
            self.assertFalse(list(root.glob("*.wordlyrics-inactive-*.bak")))

    def test_error_after_publication_rolls_back_owned_output_and_keeps_truthful_counts(self):
        with tempfile.TemporaryDirectory() as d:
            run,root,home,audio=self.run_fake(d,commit_failure=True)
            self.assertEqual(audio.with_suffix(".elrc").read_bytes(),file_bytes(OLD))
            self.assertTrue(audio.with_suffix(".ttml").exists());self.assertFalse(run.written)
            self.assertTrue(run.check["clean"]);self.assertFalse(run.counts.get("timed"))

    def test_ordinary_generation_still_protects_preferred_files_and_custom_source_can_override(self):
        with tempfile.TemporaryDirectory() as d:
            audio=Path(d)/"Artist - Song.mp3";audio.write_bytes(b"audio")
            audio.with_suffix(".ttml").write_bytes(b"old")
            audio.with_suffix(".lyrics-source.txt").write_text("invalid pin")
            with patch.object(L,"embedded",return_value=({"seconds":100},[])):
                s=L.read_song(str(audio),audio.name,5,redo=True)
                self.assertIn("protected",s.skip);self.assertEqual(s.title,"Song")
                s=L.read_song(str(audio),audio.name,5,redo=True,retime_mode="fresh",repair_lyrics=True,ignore_source=True)
                self.assertFalse(s.skip);self.assertFalse(s.text);self.assertFalse(s.lyrics_url)
                s=L.read_song(str(audio),audio.name,5,redo=True,retime_mode="fresh",repair_lyrics=True)
                self.assertIn("invalid lyrics-source",s.skip)

    def test_unscoped_repair_and_launch_arbitrary_flags_are_rejected(self):
        with tempfile.TemporaryDirectory() as d,redirect_stdout(StringIO()),patch("wordlyrics.pipeline.Run") as run:
            self.assertEqual(cli.main([d,"--repair-lyrics","--redo","--retime-mode","fresh","--confirm-redo","--yes"]),2)
            run.assert_not_called()
            with patch.dict(os.environ,WORDLYRICS_LAUNCH_REQUEST=json.dumps(["run",d,"--yes"])):
                self.assertEqual(cli.main(["launch"]),2)


class ReuseInstalledPackages(unittest.TestCase):
    def test_exact_working_packages_need_no_index_even_without_ready_marker(self):
        with tempfile.TemporaryDirectory() as d,redirect_stdout(StringIO()):
            app=Path(d);private=app/".python";private.mkdir()
            with patch.object(firststart,"APP",str(app)),patch.object(firststart,"PYDIR",str(private)), \
                 patch.object(firststart,"write_pth"),patch.object(firststart,"works",return_value=SimpleNamespace(returncode=0,stdout="ready",stderr="")), \
                 patch.object(firststart,"versions_present",return_value=True),patch.object(firststart,"with_each_index") as indexes, \
                 patch.object(firststart,"fetch") as download,patch.dict(os.environ,WORDLYRICS_REPAIR_PACKAGES=""):
                self.assertEqual(firststart.main(),0)
                indexes.assert_not_called();download.assert_not_called()
                self.assertTrue(list(private.glob("ready-*.txt")))

    def test_missing_or_mismatched_package_versions_do_not_claim_offline_readiness(self):
        with tempfile.TemporaryDirectory() as d:
            req=Path(d)/"requirements.txt";req.write_text("numpy==2.5.3 --hash=sha256:synthetic\n# comment\n",encoding="utf-8")
            with patch.object(firststart,"REQ",str(req)),patch.object(firststart,"works",return_value=SimpleNamespace(returncode=1)) as check:
                self.assertFalse(firststart.versions_present())
                self.assertIn("importlib.metadata",check.call_args.args[0]);self.assertIn("2.5.3",check.call_args.args[0])
                check.return_value.returncode=0;self.assertTrue(firststart.versions_present())
                req.write_text("# empty\n",encoding="utf-8");self.assertFalse(firststart.versions_present())


if __name__=="__main__":unittest.main()
