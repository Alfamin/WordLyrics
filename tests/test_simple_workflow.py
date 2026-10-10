"""User-visible regressions: multiple roots, no hidden Start prompt, truthful results and connectivity."""
from io import StringIO
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import queue
import socket
import ssl
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error

from wordlyrics import __main__ as cli, folders, library as L, menu, network, outcome, progress, sources, fetch
from wordlyrics.pipeline import Run
from wordlyrics.ui import Screen


class FolderWorkflow(unittest.TestCase):
    def test_plain_elrc_repair_needs_explicit_choice_then_runs_without_hidden_start(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'music';root.mkdir();home=Path(d)/'app'
            song=L.Song(str(root/'one.mp3'),'one.mp3',previous_tier=L.PLAIN,retime_path=str(root/'one.elrc'))
            for reply,expected in [('0',False),('1',True)]:
                replies=iter([reply]);executor=Mock(return_value=4)
                m=menu.Menu(str(home),[str(root)],executor,reader=lambda _:next(replies),writer=Mock())
                m.job([song])
                self.assertEqual(executor.called,expected)
                if expected:self.assertIn('--redo',executor.call_args.args[0])

    def test_opaque_player_file_is_protected_and_not_claimed_as_verified_word_timing(self):
        with tempfile.TemporaryDirectory() as d:
            song=Path(d)/'one.mp3';song.write_bytes(b'audio');song.with_suffix('.lyricsfile').write_bytes(b'opaque')
            item=L.read_song(str(song),song.name,5)
            self.assertIn('not verified',item.skip)
            self.assertNotEqual(item.tier,L.WORD)

    def test_persian_digits_in_paths_are_preserved_but_menu_digits_work(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'آهنگ۱.mp3';p.write_bytes(b'audio')
            replies=iter([str(p),'۱'])
            m=menu.Menu(d,[d],Mock(),reader=lambda _:next(replies),writer=Mock())
            self.assertEqual(m.ask('path',numbers=False),str(p))
            self.assertEqual(m.ask('option'),'1')

    def test_noctis_additional_formats_are_included(self):
        self.assertTrue({'.alac','.aifc','.asf','.dsf','.dff'} <= L.AUDIO_EXT)

    def test_all_roots_deduplicated_and_missing_reported(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);a=base/'Music';b=base/'Telegram downloads';a.mkdir();b.mkdir();(a/'album').mkdir()
            config=base/'settings.json'
            config.write_text(json.dumps({'musicFolders':[str(a),str(b),str(a/'album'),str(a),str(base/'missing')]}))
            roots,notes=folders.discover(str(config))
            self.assertEqual(roots,[str(a),str(b)])
            self.assertEqual(len(notes),1)
            self.assertIn('missing',notes[0])

    def test_invalid_settings_is_actionable(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'settings.json';p.write_text('{broken')
            roots,notes=folders.discover(str(p))
            self.assertEqual(roots,[]);self.assertIn('Choose a folder',notes[0])

    def test_one_noctis_root_needs_no_folder_prompt(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'music';root.mkdir()
            with patch.object(folders,'discover',return_value=([str(root)],[])):
                m=menu.Menu(str(Path(d)/'app'),[],Mock(),reader=Mock(side_effect=AssertionError('unexpected prompt')),writer=Mock())
                self.assertEqual(m.roots(),[str(root)])

    def test_literal_environment_variable_text_in_a_folder_name_is_not_expanded(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'Telegram %TEMP% downloads';root.mkdir()
            self.assertEqual(folders.usable(str(root)),(str(root),''))

    def test_multiple_noctis_roots_selection_and_scan_all(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);roots=[base/'music',base/'Telegram downloads']
            for root in roots:
                root.mkdir();(root/'one.mp3').write_bytes(b'fixture')
            replies=iter(['1'])
            with patch.object(folders,'discover',return_value=([str(p) for p in roots],[])),patch.object(L,'embedded',return_value=({'seconds':120,'artist':'Example','title':'One'},[])):
                m=menu.Menu(str(base/'app'),[],Mock(),reader=lambda _:next(replies),writer=Mock())
                songs=m.scan()
            self.assertEqual(len(songs),2)
            self.assertEqual(set(m.origin.values()),set(map(str,roots)))

    def test_generation_runs_once_per_root_without_extra_start_question(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);a=base/'music';b=base/'Telegram downloads';a.mkdir();b.mkdir()
            calls=[]
            def execute(args):
                calls.append(args)
                Path(args[args.index('--result')+1]).write_text(json.dumps({'summary':{'message':'1 song received word timestamps.','timed':1,'needs_attention':0},'run_folder':str(base/'report')}))
                return 0
            m=menu.Menu(str(base/'app'),[str(a),str(b)],execute,reader=Mock(side_effect=AssertionError('hidden confirmation')),writer=Mock())
            songs=[L.Song(str(a/'one.mp3'),'one.mp3'),L.Song(str(b/'two.mp3'),'two.mp3')]
            m.origin={songs[0].path:str(a),songs[1].path:str(b)}
            m.job(songs)
            self.assertEqual([v[0] for v in calls],[str(a),str(b)])
            for call in calls:
                selected=Path(call[call.index('--songs-from')+1]).read_text().splitlines()
                self.assertEqual(len(selected),1)
                self.assertIn('--yes',call)
                self.assertNotIn('--redo',call)

    def test_nonzero_status_is_not_presented_as_success(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'music';root.mkdir();writer=Mock()
            m=menu.Menu(str(Path(d)/'app'),[str(root)],Mock(return_value=4),reader=Mock(),writer=writer)
            m.job([L.Song(str(root/'one.mp3'),'one.mp3')])
            text='\n'.join(v.args[0] for v in writer.call_args_list)
            self.assertIn('Success has not been confirmed',text)
            self.assertNotIn('Run finished',text)

    def test_plain_elrc_is_not_counted_as_word_timestamps(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'song.mp3';p.write_bytes(b'a');p.with_suffix('.elrc').write_text('alpha beta\n')
            with patch.object(L,'embedded',return_value=({'seconds':120},[])):
                s=L.read_song(str(p),p.name,1,redo=True)
            self.assertEqual(s.previous_tier,L.PLAIN)
            self.assertTrue(s.retime_path)


class NetworkAndOutcome(unittest.TestCase):
    def test_precise_error_codes_without_sensitive_messages(self):
        cases=[(urllib.error.URLError(socket.gaierror(-2,'secret.invalid')), 'DNS_FAILED'),
               (TimeoutError('secret'), 'TIMEOUT'),(ssl.SSLError('secret'), 'TLS_FAILED'),
               (ValueError('secret'), 'INVALID_RESPONSE')]
        for error,expected in cases:
            with self.subTest(expected=expected):
                code,message=network.explain(error)
                self.assertEqual(code,expected);self.assertNotIn('secret',message)

    def test_http_404_means_reachable_and_403_is_blocked(self):
        for code,ok,expected in [(404,True,'REACHABLE'),(403,False,'HTTP_403'),(429,False,'RATE_LIMIT')]:
            result=network.probe('LRCLIB',Mock(side_effect=urllib.error.HTTPError('https://test.invalid',code,'private',{},None)))
            self.assertEqual((result['ok'],result['code']),(ok,expected))

    def test_provider_failure_circuit_keeps_fast_retries_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            client=fetch.Client(d,provider_config={});client.interval=0
            with patch('urllib.request.urlopen',side_effect=urllib.error.HTTPError('https://test.invalid',403,'secret',{},None)) as open_url:
                for i in range(5):self.assertEqual(client.get('/get',{'track_name':str(i)})[0],'error')
            self.assertEqual(open_url.call_count,1)
            self.assertIn('HTTP_403',client.network_failures['LRCLIB'])
            self.assertEqual(list(Path(d).glob('*.json')),[])

    def test_zero_timestamp_and_partial_results_are_clear(self):
        songs=[L.Song('one.mp3','one.mp3',result={'status':'network_error'}),L.Song('two.mp3','two.mp3',result={'status':'no_lyrics'})]
        run=SimpleNamespace(songs=songs,not_usable=[],check=None)
        summary=outcome.summarize(run)
        self.assertEqual(summary['timed'],0);self.assertTrue(summary['incomplete'])
        self.assertIn('No new word timestamps',summary['message'])
        songs.append(L.Song('three.mp3','three.mp3',result={'status':'timed'}))
        self.assertEqual(outcome.summarize(run)['needs_attention'],2)

    def test_progress_file_has_counts_and_no_lyrics(self):
        with tempfile.TemporaryDirectory() as d:
            song=L.Song('one.mp3','one.mp3',text='PRIVATE LYRIC',result={'status':'timed'})
            run=SimpleNamespace(screen=Screen('test',plain=True),songs=[song],only_songs=None,run_dir=d)
            stage=run.screen.stage('Time the words');stage.start();stage.total=2;stage.done=1
            target=Path(d)/'progress.json';progress.Writer(str(target),run).write(final=True)
            raw=target.read_text();data=json.loads(raw)
            self.assertNotIn('PRIVATE LYRIC',raw)
            self.assertEqual(data['completed'],1);self.assertTrue(data['finished'])

    def test_no_lyrics_does_not_download_or_load_models(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'music';root.mkdir();song=root/'one.mp3';song.write_bytes(b'audio')
            home=Path(d)/'app';opts=cli.parser().parse_args([str(root),'--yes','--offline','--no-open','--plain-output','--backup-to',str(Path(d)/'backup')])
            run=Run(str(root),opts,str(home))
            with patch.object(L,'embedded',return_value=({'seconds':120},[])),patch.object(run,'get_models',side_effect=AssertionError('model download')),patch.object(run,'start_engine',side_effect=AssertionError('model load')),redirect_stdout(StringIO()):
                run.run()
            self.assertEqual(run.songs[0].result['status'],'no_lyrics')


if __name__=='__main__':unittest.main()
