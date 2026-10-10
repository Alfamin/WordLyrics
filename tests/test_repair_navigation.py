"""Terminal paste, nested Back navigation and recording-aware lyric choices."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

from wordlyrics import __main__ as cli,drafts,fetch,guides,library as L,menu

SYNC='[00:01.00]alpha beta\n[00:04.00]Café فارسی\n[00:07.00]gamma delta'


class PasteTests(unittest.TestCase):
    def fixture(self,d):
        root=Path(d)/'music';root.mkdir();audio=root/'one.mp3';audio.write_bytes(b'fixture')
        return root,L.Song(str(audio),audio.name,seconds=100)

    def test_terminal_paste_and_file_input_produce_identical_guides_and_words(self):
        with tempfile.TemporaryDirectory() as d:
            root,song=self.fixture(d);home=Path(d)/'app';record=[]
            def capture(songs,**options):record.append(Path(options['lyrics_file']).read_text(encoding='utf-8'))
            replies=iter(['2','','   ',SYNC.splitlines()[0],'',SYNC.splitlines()[1],'','','',SYNC.splitlines()[2],'.done'])
            output=Mock();m=menu.Menu(str(home),[str(root)],Mock(),reader=lambda _:next(replies),writer=output);m.job=capture
            m.custom_lyrics(song)
            supplied=Path(d)/'supplied.lrc';supplied.write_text('\n\n'+SYNC+'\n\n',encoding='utf-8')
            replies=iter(['3',str(supplied)]);m.read=lambda _:next(replies);m.custom_lyrics(song)
            self.assertEqual(record[0],SYNC+'\n');self.assertEqual(guides.rows(record[0])[0],guides.rows(record[1])[0])
            self.assertNotIn('\n\n',record[0])
            printed='\n'.join(call.args[0] for call in output.call_args_list)
            self.assertIn('3 usable line timestamps',printed);self.assertIn('.done',printed)

    def test_plain_paste_reports_zero_guides_before_timing(self):
        with tempfile.TemporaryDirectory() as d:
            root,song=self.fixture(d);replies=iter(['2','alpha beta','','gamma delta','.done']);output=Mock()
            m=menu.Menu(str(Path(d)/'app'),[str(root)],Mock(),reader=lambda _:next(replies),writer=output);m.job=Mock()
            m.custom_lyrics(song)
            self.assertIn('0 usable line timestamps','\n'.join(c.args[0] for c in output.call_args_list))
            self.assertTrue(m.job.called)

    def test_cancel_and_empty_paste_never_start_a_job(self):
        with tempfile.TemporaryDirectory() as d:
            root,song=self.fixture(d)
            for values in [['2','alpha','.cancel'],['2','','   ','.done'],['0']]:
                replies=iter(values);m=menu.Menu(d,[str(root)],Mock(),reader=lambda _:next(replies),writer=Mock());m.job=Mock()
                m.custom_lyrics(song);m.job.assert_not_called()

    def test_plain_paste_preview_explains_when_existing_guides_will_be_reused(self):
        with tempfile.TemporaryDirectory() as d:
            root,song=self.fixture(d);Path(song.path).with_suffix('.elrc').write_text(SYNC,encoding='utf-8')
            replies=iter(['2',*guides.words(SYNC).splitlines(),'.done']);output=Mock()
            m=menu.Menu(str(Path(d)/'app'),[str(root)],Mock(),reader=lambda _:next(replies),writer=output);m.job=Mock()
            with patch.object(L,'embedded',return_value=({'seconds':100},[])):m.custom_lyrics(song)
            printed='\n'.join(c.args[0] for c in output.call_args_list)
            self.assertIn('Using 3 existing compatible line guides',printed)

    def test_standard_input_preserves_synced_unicode_paste_without_empty_lines(self):
        # Uses actual Python input(), not a fake reader. No model/network/library writes.
        with tempfile.TemporaryDirectory() as d:
            root,song=self.fixture(d);home=Path(d)/'app';capture=Path(d)/'captured.txt'
            script='''import sys
from pathlib import Path
from wordlyrics import menu,library as L
root,home,song,out=sys.argv[1:]
m=menu.Menu(home,[root],lambda _:0)
def capture(songs,**kw):Path(out).write_text(Path(kw['lyrics_file']).read_text(encoding='utf-8'),encoding='utf-8')
m.job=capture
m.custom_lyrics(L.Song(song,Path(song).name,seconds=100))
'''
            result=subprocess.run([sys.executable,'-X','utf8','-c',script,str(root),str(home),song.path,str(capture)],
                                  input='2\n\n'+SYNC.replace('\n','\n\n\n')+'\n\n\n.done\n',text=True,
                                  encoding='utf-8',capture_output=True,cwd=Path(__file__).resolve().parents[1],timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(capture.read_text(encoding='utf-8'),SYNC+'\n')
            self.assertIn('3 usable line timestamps',result.stdout)


class NavigationTests(unittest.TestCase):
    def make(self,d,replies):
        root=Path(d)/'music';root.mkdir();audio=root/'one.mp3';audio.write_bytes(b'fixture')
        answers=iter(replies);m=menu.Menu(str(Path(d)/'app'),[str(root)],Mock(),reader=lambda _:next(answers),writer=Mock())
        return m,L.Song(str(audio),audio.name,seconds=100)

    def test_back_from_custom_menu_returns_to_song_menu(self):
        with tempfile.TemporaryDirectory() as d:
            m,s=self.make(d,['5','0','2','0']);m.job=Mock()
            with patch.object(L,'embedded',return_value=({'seconds':100},[])):m.song_actions([s])
            m.job.assert_called_once();self.assertEqual(m.job.call_args.kwargs['mode_hint'],'current')

    def test_back_from_source_menu_returns_to_song_menu(self):
        with tempfile.TemporaryDirectory() as d:
            m,s=self.make(d,['3','0','2','0']);m.job=Mock()
            with patch.object(L,'embedded',return_value=({'seconds':100},[])):m.song_actions([s])
            m.job.assert_called_once()

    def test_noctis_repair_launch_back_opens_main_menu_instead_of_exiting(self):
        with patch('wordlyrics.menu.Menu') as constructor:
            session=constructor.return_value;session.repair_path.return_value=4;session.run.return_value=0
            self.assertEqual(cli._main(['repair-song','selected.mp3']),0)
            session.repair_path.assert_called_once_with('selected.mp3',lyrics_file=None)
            session.run.assert_called_once()

    def test_noctis_editor_launch_keeps_session_after_its_job(self):
        with patch('wordlyrics.menu.Menu') as constructor:
            session=constructor.return_value;session.run.return_value=0
            self.assertEqual(cli._main(['repair-song','selected.mp3','--lyrics-file','draft.txt']),0)
            session.repair_path.assert_called_once_with('selected.mp3',lyrics_file='draft.txt');session.run.assert_called_once()

    def test_noctis_resume_launch_back_also_returns_to_main_menu(self):
        with patch('wordlyrics.menu.Menu') as constructor:
            session=constructor.return_value;session.run.return_value=0
            self.assertEqual(cli._main(['resume','saved-job']),0)
            session.resume_job.assert_called_once_with('saved-job');session.run.assert_called_once()

    def test_failure_notice_survives_screen_redraw_without_claiming_success(self):
        with tempfile.TemporaryDirectory() as d:
            m,s=self.make(d,['2','1','0']);m.execute=Mock(return_value=4)
            with patch.object(L,'embedded',return_value=({'seconds':100},[])):m.song_actions([s])
            self.assertIn('Success has not been confirmed',m.last_notice)

    @unittest.skipUnless(os.name=='nt','Windows launcher behavior')
    def test_interactive_launcher_exit_has_no_second_press_any_key_pause(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'wordlyrics').mkdir();(root/'wordlyrics'/'__init__.py').write_text('')
            (root/'wordlyrics'/'runner.py').write_text("print('MENU_EXIT')\n")
            launcher=root/'WordLyrics.bat';launcher.write_bytes((Path(__file__).resolve().parents[1]/'WordLyrics.bat').read_bytes())
            env=dict(os.environ,WORDLYRICS_PYTHON=sys.executable);env.pop('WORDLYRICS_NO_PAUSE',None)
            for args in [[],['menu'],['repair-song'],['launch']]:
                result=subprocess.run([os.environ.get('COMSPEC',r'C:\Windows\System32\cmd.exe'),'/d','/c',str(launcher),*args],
                                      stdin=subprocess.DEVNULL,capture_output=True,text=True,env=env,cwd=root,timeout=10,
                                      creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.strip(),'MENU_EXIT')


class RecordingChoices(unittest.TestCase):
    def song(self):return L.Song('one.flac','one.flac',artist='Kanye West, Pusha T',title='Runaway',album='My Beautiful Dark Twisted Fantasy',seconds=547.7)

    def record(self,id,seconds=548,**more):
        row={'id':id,'artistName':'Kanye West','trackName':'Runaway','albumName':'My Beautiful Dark Twisted Fantasy','duration':seconds,
             'syncedLyrics':'\n'.join('[00:%02d.00]alpha beta gamma'%i for i in range(1,8))}
        row.update(more);return row

    def test_exact_and_artist_title_queries_rank_correct_recordings_before_hundreds_of_seconds_off(self):
        exact=self.record(10);matches=[self.record(1,230),self.record(2,338),self.record(3,551),self.record(4,548),self.record(5,548,artistName='Another Artist')]
        client=Mock();client.get.side_effect=[('ok',exact),('ok',matches)]
        with tempfile.TemporaryDirectory() as d,patch('wordlyrics.fetch.Client',return_value=client):
            rows=drafts.lrclib_results(d,self.song(),'Runaway')
        self.assertEqual({row['id'] for row in rows[:2]},{10,4})
        self.assertTrue(all(row['seconds_off']<1 for row in rows[:2]))
        self.assertEqual(client.get.call_args_list[0].args[0],'/get')
        self.assertEqual(client.get.call_args_list[1].args,('/search',{'artist_name':'Kanye West','track_name':'Runaway'}))
        self.assertFalse(next(row for row in rows if row['id']==1)['near_match'])

    def test_duplicate_exact_result_is_listed_once_and_censored_invalid_sources_stay_filtered(self):
        exact=self.record(10);client=Mock();client.get.side_effect=[('ok',exact),('ok',[exact,self.record(12,syncedLyrics='',plainLyrics='f*** '*40),self.record(13,instrumental=True)])]
        with tempfile.TemporaryDirectory() as d,patch('wordlyrics.fetch.Client',return_value=client):
            rows=drafts.lrclib_results(d,self.song(),'')
        self.assertEqual([row['id'] for row in rows],[10])

    def test_picker_hides_wrong_versions_by_default_and_labels_duration_separately_from_difference(self):
        with tempfile.TemporaryDirectory() as d:
            client=Mock();client.get.side_effect=[('ok',self.record(10)),('ok',[self.record(1,230)])]
            with patch('wordlyrics.fetch.Client',return_value=client):rows=drafts.lrclib_results(d,self.song(),'')
            answers=iter(['','0']);output=Mock();m=menu.Menu(d,[],Mock(),reader=lambda _:next(answers),writer=output)
            with patch.object(drafts,'lrclib_results',return_value=rows):m.lrclib_choice(self.song())
            text='\n'.join(c.args[0] for c in output.call_args_list)
            self.assertIn('547.7 seconds',text);self.assertIn('difference 0.3s',text)
            self.assertNotIn('230s',text);self.assertIn('other versions',text)

    def test_alternate_view_is_explicit_and_back_does_not_start_timing(self):
        with tempfile.TemporaryDirectory() as d:
            client=Mock();client.get.side_effect=[('ok',self.record(10)),('ok',[self.record(1,230)])]
            with patch('wordlyrics.fetch.Client',return_value=client):rows=drafts.lrclib_results(d,self.song(),'')
            answers=iter(['','a','0']);output=Mock();m=menu.Menu(d,[],Mock(),reader=lambda _:next(answers),writer=output);m.job=Mock()
            with patch.object(drafts,'lrclib_results',return_value=rows):m.lrclib_choice(self.song())
            self.assertIn('difference 317.7s','\n'.join(c.args[0] for c in output.call_args_list));m.job.assert_not_called()

    def test_pages_keep_stable_result_numbers_and_selection(self):
        with tempfile.TemporaryDirectory() as d:
            client=Mock();client.get.side_effect=[('ok',self.record(1)),('ok',[self.record(i) for i in range(1,10)])]
            with patch('wordlyrics.fetch.Client',return_value=client):rows=drafts.lrclib_results(d,self.song(),'')
            answers=iter(['','n','4']);m=menu.Menu(d,[],Mock(),reader=lambda _:next(answers),writer=Mock());m.job=Mock()
            with patch.object(drafts,'lrclib_results',return_value=rows),patch.object(menu.shutil,'get_terminal_size',return_value=os.terminal_size((80,24))):
                m.lrclib_choice(self.song())
            self.assertEqual(guides.rows(Path(m.job.call_args.kwargs['lyrics_file']).read_text())[0],guides.rows(rows[3]['chosen_text'])[0])


if __name__=='__main__':unittest.main()
