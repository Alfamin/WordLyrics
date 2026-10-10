"""Simulated computer states, permission plans and resumable jobs; no real elevation/models."""
from contextlib import redirect_stdout
from io import StringIO
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import time
import urllib.error
from unittest.mock import Mock,patch

from wordlyrics import __main__ as cli,library as L,permissions,recovery,resume,runner,fetch,backup,menu
from wordlyrics.engine import Engine
from wordlyrics.pipeline import Run

WORD='[00:01.00]<00:01.00>alpha<00:02.00> <00:02.00>beta<00:03.00>\n'


class ComputerStates(unittest.TestCase):
    def test_expired_positive_cache_is_used_only_when_connection_fails(self):
        with tempfile.TemporaryDirectory() as d:
            client=fetch.Client(d,provider_config={});params={'track_name':'Example','artist_name':'Example'}
            url=fetch.API+'/get?'+fetch.urllib.parse.urlencode(sorted(params.items()))
            path=Path(client._cache(url));path.write_text(json.dumps({'status':'ok','data':{'id':1},'ts':time.time()-8*86400}))
            with patch('urllib.request.urlopen',side_effect=urllib.error.HTTPError('https://example.invalid',403,'blocked',{},None)):
                self.assertEqual(client.get('/get',params),('ok',{'id':1}))
            self.assertIn('CACHE_FALLBACK',client.recovery_notes[0])
            client=fetch.Client(d,provider_config={})
            with patch('urllib.request.urlopen',side_effect=urllib.error.HTTPError('https://example.invalid',404,'missing',{},None)):
                self.assertEqual(client.get('/get',params),('notfound',None))

    def test_working_gpu_is_kept_when_cpu_test_fails(self):
        engine=Engine.__new__(Engine);engine.ort=SimpleNamespace(__version__='fake');engine.gpu='fake';engine.sessions={}
        engine.where={};engine._drop=Mock();engine.say=Mock()
        engine._bench=Mock(side_effect=lambda model,where:.01 if where=='gpu' else (_ for _ in ()).throw(RuntimeError('CPU failed')))
        engine._auto(None)
        self.assertTrue(all(where=='gpu' for where in engine.where.values()))
    def test_supported_minimum_and_newer_windows(self):
        for version in [(10,0,18362),(10,0,19045),(10,0,22000),(10,0,26100),(10,0,99999)]:
            with self.subTest(version=version):self.assertEqual(recovery.computer_supported(version=version),'')
        self.assertIn('WINDOWS_UPDATE_REQUIRED',recovery.computer_supported(version=(10,0,17763)))
        self.assertIn('UNSUPPORTED_ARCHITECTURE',recovery.computer_supported(bits=32))
        self.assertIn('UNSUPPORTED_SYSTEM',recovery.computer_supported(system='Linux'))

    def test_gpu_failure_retries_on_cpu_once(self):
        engine=Engine.__new__(Engine);engine.where={'model':'gpu'};engine.gpu_failures=0
        engine._drop=Mock();engine._rest=Mock();engine.say=Mock()
        gpu=Mock();gpu.run.side_effect=RuntimeError('driver changed')
        cpu=Mock();cpu.run.return_value=['valid result']
        engine._session=Mock(side_effect=lambda model,where:gpu if where=='gpu' else cpu)
        self.assertEqual(engine._run('model',None,{}),['valid result'])
        self.assertEqual(engine.where['model'],'cpu');self.assertEqual(cpu.run.call_count,1)
        self.assertIn('GPU_FALLBACK',engine.note)
        cpu.run.side_effect=RuntimeError('CPU memory insufficient')
        with self.assertRaises(RuntimeError):engine._run('model',None,{})
        self.assertEqual(cpu.run.call_count,2)

    def test_private_dependency_repair_is_bounded_and_global_python_is_protected(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d);private=home/'.python';private.mkdir();ready=private/'ready-1.txt';ready.write_text('old')
            repaired=[False]
            def imports(name):
                if name=='onnxruntime' and not repaired[0]:raise ImportError('damaged package')
            def fix():repaired[0]=True;return 0
            repair=Mock(side_effect=fix)
            with redirect_stdout(StringIO()):self.assertTrue(runner.ensure(d,python=str(private/'python.exe'),importer=imports,repair=repair))
            repair.assert_called_once();self.assertFalse(ready.exists())
            repaired[0]=False;repair.reset_mock()
            with redirect_stdout(StringIO()):self.assertFalse(runner.ensure(d,python=str(home/'global'/'python.exe'),importer=imports,repair=repair))
            repair.assert_not_called()

    def test_failed_package_repair_does_not_loop(self):
        with tempfile.TemporaryDirectory() as d:
            private=Path(d)/'.python';private.mkdir();fix=Mock(return_value=1)
            with redirect_stdout(StringIO()):self.assertFalse(runner.ensure(d,python=str(private/'python.exe'),importer=Mock(side_effect=ImportError),repair=fix))
            fix.assert_called_once()

    def test_corrupt_model_is_preserved_and_only_bad_model_is_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d);(path/'bad.onnx').write_bytes(b'bad!');(path/'good.onnx').write_bytes(b'good')
            known=[{'file':name,'sha256':hashlib.sha256(b'good').hexdigest()} for name in ['bad.onnx','good.onnx']]
            download=Mock(side_effect=lambda model,folder:(Path(folder)/model['file']).write_bytes(b'good'))
            self.assertEqual(recovery.repair_models(d,known,download),['bad.onnx'])
            download.assert_called_once()
            self.assertEqual(next(path.glob('*.damaged-*')).read_bytes(),b'bad!')
            self.assertEqual(recovery.repair_models(d,known,download),[])

    def test_permission_error_recognizes_wrapped_failure(self):
        try:
            try:raise PermissionError('another user owns it')
            except PermissionError as e:raise RuntimeError('backup cannot be created') from e
        except RuntimeError as wrapped:self.assertTrue(recovery.permission_error(wrapped))
        self.assertFalse(recovery.permission_error(OSError('disk disconnected')))


class PermissionPlans(unittest.TestCase):
    def test_windows_long_path_prefix_does_not_break_scope_checks(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'music';root.mkdir();child=str(root/'nested'/'song.mp3')
            self.assertTrue(backup.inside('\\\\?\\'+child,str(root)))
            self.assertFalse(backup.inside('\\\\?\\'+str(Path(d)/'outside.mp3'),str(root)))
    def test_local_folder_plan_and_system_or_foreign_scope_rejection(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'music';root.mkdir();song=root/'one.mp3';song.write_bytes(b'audio')
            self.assertEqual(permissions.plan([str(song),str(song)],str(root)),[str(song)])
            with self.assertRaises(ValueError):permissions.plan([str(Path(d))],str(root))
            with self.assertRaises(ValueError):permissions.plan([str(root)],str(root),blocked=[str(root)])
            with self.assertRaises(ValueError):permissions.plan([str(root/'missing.mp3')],str(root))
            with self.assertRaises(ValueError):permissions.plan([str(Path(d).anchor)],str(root))

    def test_request_grants_original_user_not_the_elevated_administrator(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'music';source.mkdir();home=Path(d)/'app';audio=source/'one.mp3';audio.write_bytes(b'unchanged')
            requests=[]
            def elevation(request,result):
                data=json.loads(request.read_text());requests.append(data)
                result.write_text(json.dumps({'message':'fixed original user'}))
            with patch('wordlyrics.permissions.subprocess.check_output',return_value='"normal user","S-1-5-21-1-2-3-1001"'):
                message=permissions.repair(str(home),str(source),[str(source)],launch=elevation)
            self.assertEqual(requests[0]['sid'],'S-1-5-21-1-2-3-1001')
            self.assertEqual(message,'fixed original user');self.assertEqual(audio.read_bytes(),b'unchanged')

    def test_canceled_elevation_preserves_work_and_does_not_retry_automatically(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'music';source.mkdir();launch=Mock()
            with patch('wordlyrics.permissions.subprocess.check_output',return_value='"user","S-1-5-21-1-2-3-1001"'):
                message=permissions.repair(str(Path(d)/'app'),str(source),[str(source)],launch=launch)
            launch.assert_called_once();self.assertIn('job is saved',message)


class SavedJobs(unittest.TestCase):
    def test_successful_permission_fix_continues_the_saved_scope_automatically(self):
        with tempfile.TemporaryDirectory() as d:
            home,run,paths=self.fixture(d);answers=iter(['2','1','0']);execute=Mock(return_value=4)
            m=menu.Menu(str(home),[run.source],execute,reader=lambda _:next(answers),writer=Mock())
            with patch.object(permissions,'repair',return_value={'ok':True,'message':'fixed original user'}) as fix:
                m.resume_job(run.run_dir)
            fix.assert_called_once();execute.assert_called_once()
            args=execute.call_args.args[0];chosen=Path(args[args.index('--songs-from')+1]).read_text().splitlines()
            self.assertEqual(chosen,list(map(str,paths[1:])))

    def test_canceled_permission_fix_does_not_start_processing(self):
        with tempfile.TemporaryDirectory() as d:
            home,run,paths=self.fixture(d);answers=iter(['2','1','0']);execute=Mock()
            m=menu.Menu(str(home),[run.source],execute,reader=lambda _:next(answers),writer=Mock())
            with patch.object(permissions,'repair',return_value={'ok':False,'message':'UAC canceled; job saved'}):m.resume_job(run.run_dir)
            execute.assert_not_called();self.assertTrue(resume.pending(str(home),run.run_dir)[1])
    def test_unreadable_subfolder_is_saved_and_discovered_after_access_is_fixed(self):
        with tempfile.TemporaryDirectory() as d:
            home,run,paths=self.fixture(d);album=Path(run.source)/'blocked album';album.mkdir();unknown=album/'hidden.mp3';unknown.write_bytes(b'audio')
            run.unreadable=['blocked album'];resume.save(run)
            original=L.walk
            def denied(root,*args,**kwargs):
                if root==str(album):
                    kwargs.get('unreadable',[]).append('.')
                    return [],[]
                return original(root,*args,**kwargs)
            with patch.object(L,'walk',side_effect=denied):
                rows=resume.pending(str(home),run.run_dir)[1]
                self.assertTrue(any(row.get('folder') for row in rows))
            rows=resume.pending(str(home),run.run_dir)[1]
            self.assertIn(str(unknown),[row['path'] for row in rows])
            self.assertNotIn(str(paths[0]),[row['path'] for row in rows])

    def test_failed_startup_saves_job_without_loading_models(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'music';root.mkdir();audio=root/'one.mp3';audio.write_bytes(b'audio');home=Path(d)/'app';out=Path(d)/'result.json'
            runner.save_failed_startup(str(home),[str(root),'--yes','--result',str(out)],'ENVIRONMENT_REPAIR_FAILED')
            result=json.loads(out.read_text());self.assertEqual(result['exit_code'],70)
            self.assertTrue((Path(result['run_folder'])/'request.json').exists())

    def fixture(self,d,redo=False):
        root=Path(d)/'music';root.mkdir();home=Path(d)/'app'
        paths=[root/(name+'.mp3') for name in ('complete','failed','not reached')]
        for path in paths:path.write_bytes(b'audio stays untouched')
        opts=cli.parser().parse_args([str(root),'--yes']);opts.songs=list(map(str,paths));opts.redo=redo;opts.speed='full'
        run=Run(str(root),opts,str(home));Path(run.run_dir).mkdir(parents=True)
        resume.save(run,list(map(str,paths)))
        paths[0].with_suffix('.elrc').write_text(WORD)
        with (Path(run.run_dir)/'log.jsonl').open('w') as log:
            for path,status in zip(paths,['timed','rejected','not_reached']):
                log.write(json.dumps({'song':path.name,'status':status,'reason':'recording mismatch' if status=='rejected' else 'stopped'})+'\n')
        return home,run,paths

    def test_only_unfinished_songs_resume_after_restart(self):
        with tempfile.TemporaryDirectory() as d:
            home,run,paths=self.fixture(d)
            _,rows=resume.pending(str(home),run.run_dir)
            self.assertEqual([row['path'] for row in rows],list(map(str,paths[1:])))
            def execute(args):
                selected=Path(args[args.index('--songs-from')+1]).read_text().splitlines()
                self.assertEqual(selected,list(map(str,paths[1:])))
                for path in selected:Path(path).with_suffix('.elrc').write_text(WORD)
                Path(args[args.index('--result')+1]).write_text(json.dumps({'songs':[{'path':p,'status':'timed'} for p in selected]}))
                return 0
            self.assertEqual(resume.submit(str(home),run.run_dir,execute),0)
            self.assertEqual(resume.pending(str(home),run.run_dir)[1],[])
            for path in paths:self.assertEqual(path.read_bytes(),b'audio stays untouched')

    def test_tampered_out_of_scope_job_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            home,run,paths=self.fixture(d);request=Path(run.run_dir)/'request.json';data=json.loads(request.read_text())
            data['songs']=[str(Path(d)/'outside.mp3')];request.write_text(json.dumps(data))
            with self.assertRaises(ValueError):resume.submit(str(home),run.run_dir,Mock())

    def test_disconnected_files_are_kept_as_pending(self):
        with tempfile.TemporaryDirectory() as d:
            home,run,paths=self.fixture(d);paths[1].unlink()
            self.assertIn(str(paths[1]),[row['path'] for row in resume.pending(str(home),run.run_dir)[1]])

    def test_redo_failure_keeps_original_pending_until_changed_or_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            home,run,paths=self.fixture(d,redo=True);lyric=paths[1].with_suffix('.elrc');lyric.write_text(WORD)
            run.songs=[L.Song(str(paths[1]),paths[1].name,retime_sha256=hashlib.sha256(lyric.read_bytes()).hexdigest())]
            resume.save(run)
            self.assertIn(str(paths[1]),[row['path'] for row in resume.pending(str(home),run.run_dir)[1]])
            lyric.write_text(WORD.replace('01.00','04.00'))
            self.assertNotIn(str(paths[1]),[row['path'] for row in resume.pending(str(home),run.run_dir)[1]])


if __name__=='__main__':unittest.main()
