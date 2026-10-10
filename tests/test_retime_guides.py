"""Regression cases for losing line guides, substituting words and misreporting confidence."""
from io import StringIO
from contextlib import redirect_stdout
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import numpy as np

from wordlyrics import __main__ as cli,decide,drafts,fetch,guides,library as L,menu,resume,timing as T
from wordlyrics.pipeline import Run,library_home

LINES=['alpha beta','bravo charlie','delta echo','foxtrot golf','hotel india','juliet kilo','lima mike','november oscar','papa quebec','romeo sierra']
LRC='\n'.join('[%02d:%02d.00]%s'%(0,2+i*3,line) for i,line in enumerate(LINES))
ELRC='\n'.join('[00:%02d.00]<00:%02d.00>%s<00:%02d.50>'%(2+i*3,2+i*3,line,2+i*3) for i,line in enumerate(LINES))


class GuideInput(unittest.TestCase):
    def read(self,root,**kwargs):
        audio=root/'one.mp3';audio.write_bytes(b'audio')
        with patch.object(L,'embedded',return_value=({'seconds':60,'artist':'Example','title':'One'},[])):
            return L.read_song(str(audio),audio.name,5,**kwargs)

    def test_current_guided_and_unanchored_are_distinct_and_fresh_ignores_original(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'one.elrc').write_text(ELRC)
            for mode in ['current','guided','unanchored','fresh']:
                with self.subTest(mode=mode):
                    s=self.read(root,redo=True,retime_mode=mode)
                    self.assertEqual(s.tier,{'current':L.LINE,'guided':L.LINE,'unanchored':L.PLAIN,'fresh':L.NONE}[mode])
                    self.assertEqual(s.keep_words,mode!='fresh')
                    self.assertNotIn('<',s.text)
                    if mode!='fresh':self.assertEqual(guides.words(s.text),'\n'.join(LINES))

    def test_synced_custom_txt_lrc_and_elrc_inputs_retain_guides_even_in_fresh_repair(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for ext,text in [('txt',LRC),('lrc',LRC),('elrc',ELRC)]:
                with self.subTest(extension=ext):
                    p=root/('provided.'+ext);p.write_text('\ufeff'+text,encoding='utf-8')
                    s=self.read(root,redo=True,retime_mode='fresh',repair_lyrics=True,lyrics_file=str(p))
                    self.assertEqual(s.tier,L.LINE);self.assertTrue(s.keep_words)
                    self.assertEqual(guides.words(s.text),'\n'.join(LINES));self.assertNotIn('<',s.text)
                    self.assertIn('retained',s.timing_note)

    def test_existing_plain_lyrics_remain_plain(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'one.elrc').write_text('\n'.join(LINES))
            s=self.read(root,redo=True)
            self.assertEqual(s.tier,L.PLAIN);self.assertIn('from scratch',s.timing_note)

    def test_lrc_and_embedded_guides_work_without_an_elrc(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'one.lrc').write_text(LRC)
            s=self.read(root,redo=True)
            self.assertEqual(s.tier,L.LINE)
            (root/'one.lrc').unlink()
            audio=root/'one.mp3'
            with patch.object(L,'embedded',return_value=({'seconds':60},[LRC])):
                s=L.read_song(str(audio),audio.name,5,redo=True)
            self.assertEqual(s.tier,L.LINE)

    def test_existing_plain_elrc_can_reuse_matching_lrc_guides_without_replacing_words(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'one.elrc').write_text('\n'.join(LINES));(root/'one.lrc').write_text(LRC)
            s=self.read(root,redo=True)
            self.assertEqual(s.tier,L.LINE);self.assertEqual(guides.words(s.text),'\n'.join(LINES))

    def test_empty_or_heading_only_custom_file_is_rejected_before_model_loading(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for content in ['', '[Chorus]\n[ar:Example]\n']:
                p=root/'empty.txt';p.write_text(content)
                s=self.read(root,redo=True,lyrics_file=str(p))
                self.assertIn('no sung words',s.skip)

    def test_empty_custom_file_pipeline_reports_attention_without_loading_models(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'music';root.mkdir();home=Path(d)/'app'
            audio=root/'one.mp3';audio.write_bytes(b'audio');lyric=root/'one.elrc';lyric.write_text(ELRC)
            custom=Path(d)/'empty.txt';custom.write_text('[Chorus]')
            original=lyric.read_bytes()
            opts=cli.parser().parse_args([str(root),'--yes','--redo','--confirm-redo','--lyrics-file',str(custom),
                                         '--offline','--no-open','--backup-to',str(Path(d)/'backup')])
            opts.songs=[str(audio)]
            with patch('wordlyrics.pipeline.Screen',return_value=Mock()),patch('wordlyrics.pipeline.decide.low_priority'), \
                 patch.object(L,'embedded',return_value=({'seconds':60},[])),redirect_stdout(StringIO()):
                run=Run(str(root),opts,str(home));run.get_models=Mock();run.start_engine=Mock()
                run.run()
            run.get_models.assert_not_called();run.start_engine.assert_not_called()
            self.assertEqual(run.songs[0].result['status'],'not_timed')
            self.assertEqual(lyric.read_bytes(),original);self.assertFalse(run.written)

    def test_saved_genius_choice_does_not_override_keep_words_or_block_with_a_bad_pin(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'one.elrc').write_text(ELRC)
            for value in ['https://genius.com/Other-song-lyrics','invalid source']:
                (root/'one.lyrics-source.txt').write_text(value)
                kept=self.read(root,redo=True)
                self.assertFalse(kept.skip);self.assertFalse(kept.lyrics_url)
                fresh=self.read(root,redo=True,retime_mode='fresh')
                self.assertTrue(fresh.lyrics_url or fresh.skip)

    def test_plain_draft_roundtrip_retains_original_guides_and_original_file(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);audio=root/'one.mp3';audio.write_bytes(b'audio');side=root/'one.elrc';side.write_text(ELRC)
            original=side.read_bytes();song=L.Song(str(audio),audio.name)
            with patch.object(L,'embedded',return_value=({'seconds':60},[])):
                draft=Path(drafts.create(str(root/'app'),song))
                self.assertEqual(guides.rows(draft.read_text())[0],guides.rows(LRC)[0])
                self.assertNotIn('<',draft.read_text())
                s=L.read_song(str(audio),audio.name,5,redo=True,lyrics_file=str(draft))
            self.assertEqual(s.tier,L.LINE);self.assertEqual(side.read_bytes(),original)

    def test_one_plain_text_correction_can_use_the_existing_same_line_guides(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'one.elrc').write_text(ELRC)
            replacement=LINES.copy();replacement[5]='corrected custom words'
            p=root/'custom.txt';p.write_text('\n'.join(replacement))
            s=self.read(root,redo=True,lyrics_file=str(p))
            self.assertEqual(s.tier,L.LINE)
            self.assertEqual(guides.words(s.text),'\n'.join(replacement))
            self.assertEqual([t for t,_ in guides.rows(s.text)[0]],[t for t,_ in guides.rows(LRC)[0]])

    def test_large_rewrite_insertions_and_reordering_do_not_inherit_old_guides(self):
        for text in ['unrelated words\n'*10,'\n'.join(LINES+['extra verse']),'\n'.join(reversed(LINES))]:
            with self.subTest(text_length=len(text)):
                value,timed,note=guides.prepare(text,LRC,60)
                self.assertFalse(timed);self.assertEqual(value,guides.words(text))

    def test_invalid_mixed_backward_and_out_of_recording_guides_are_explained(self):
        cases=[('[00:70]alpha','invalid seconds'),('[00:02]alpha\nbeta','no line timestamp'),
               ('[00:03]alpha\n[00:01]beta','backwards'),('[01:10]alpha','outside'),
               ('[offset:-3000]\n[00:02]alpha','outside'),('[offset:1]\n[offset:2]\n[00:02]alpha','conflicting')]
        for text,reason in cases:
            with self.subTest(reason=reason):
                value,timed,note=guides.prepare(text,duration=60)
                self.assertFalse(timed);self.assertIn(reason,note)
                self.assertNotIn('[',value)

    def test_repeated_chorus_timestamps_and_global_offset_expand_in_time_order(self):
        value,timed,_=guides.prepare('[offset:500]\n[ar:Example]\n[00:01][00:05]alpha\n[00:03]beta',duration=20)
        self.assertTrue(timed)
        self.assertEqual(guides.rows(value)[0],[(1.5,'alpha'),(3.5,'beta'),(5.5,'alpha')])
        self.assertEqual(guides.words(value),'alpha\nbeta\nalpha')

    def test_unicode_punctuation_and_headings_survive_as_words(self):
        value,timed,_=guides.prepare('[Chorus]\n[00:01]Café فارسی!\n[00:04]Hello, world.',duration=10)
        self.assertTrue(timed);self.assertEqual(guides.words(value),'Café فارسی!\nHello, world.')

    def test_explicit_unanchored_choice_also_removes_supplied_file_guides(self):
        value,timed,note=guides.prepare(LRC,ELRC,60,unanchored=True)
        self.assertFalse(timed);self.assertEqual(value,'\n'.join(LINES));self.assertIn('explicitly',note)


class AttemptRouting(unittest.TestCase):
    def helper(self,mode='current'):
        return SimpleNamespace(opts=SimpleNamespace(retime_mode=mode))

    def test_provider_words_cannot_replace_current_or_custom_words(self):
        s=L.Song('one.mp3','one.mp3',tier=L.PLAIN,text='\n'.join(LINES),keep_words=True,seconds=60)
        alternative=LINES.copy();alternative[5]='different provider words'
        s.found=fetch.Found(L.LINE,guides.render([(2+i*3,line) for i,line in enumerate(alternative)]))
        attempted=Run.attempts(self.helper(),s)
        self.assertEqual(attempted[0][0],'line')
        self.assertEqual(guides.words(attempted[0][1]),s.text)
        self.assertEqual(attempted[0][2],'yours')

    def test_no_compatible_provider_guides_means_original_words_with_plain_check(self):
        s=L.Song('one.mp3','one.mp3',tier=L.PLAIN,text='\n'.join(LINES),keep_words=True,seconds=60)
        s.found=fetch.Found(L.LINE,'[00:01]unrelated song')
        self.assertEqual(Run.attempts(self.helper(),s),[('plain',s.text,'yours')])
        s.found=fetch.Found(L.PLAIN,'different online text')
        self.assertEqual(Run.attempts(self.helper(),s),[('plain',s.text,'yours')])

    def test_unanchored_choice_cannot_be_cancelled_by_a_provider(self):
        s=L.Song('one.mp3','one.mp3',tier=L.PLAIN,text='\n'.join(LINES),keep_words=True,seconds=60,found=fetch.Found(L.LINE,LRC))
        self.assertEqual(Run.attempts(self.helper('unanchored'),s),[('plain',s.text,'yours')])

    def test_guided_existing_words_do_not_use_an_online_source_at_all(self):
        s=L.Song('one.mp3','one.mp3',tier=L.LINE,text=LRC,keep_words=True,found=fetch.Found(L.PLAIN,'wrong words'))
        self.assertEqual(Run.attempts(self.helper(),s),[('line',LRC,'yours')])

    def test_fresh_lookup_still_uses_provider_words_and_audio_match_check(self):
        s=L.Song('one.mp3','one.mp3',found=fetch.Found(L.PLAIN,'fresh words',require_audio_match=True))
        self.assertEqual(Run.attempts(self.helper('fresh'),s),[('plain','fresh words','found',True)])

    def test_low_confidence_and_wrong_song_still_fail_without_guides(self):
        stats={'lines':10,'lines_unsure':4,'lines_unheard':0,'lines_aligned':8,'words':20}
        with patch.object(T,'belongs',return_value=(True,1.5)),patch.object(T,'process_plain',return_value=('candidate',[],stats,[])):
            result=decide.judge({},[('plain','custom words','yours')])
        self.assertIsNone(result['timed']);self.assertIn('not a measured',result['last']['reason'])
        self.assertEqual(result['last']['lines_unsure'],4)
        with patch.object(T,'belongs',return_value=(False,1.0)),patch.object(T,'process_plain') as alignment:
            result=decide.judge({},[('plain','wrong song','found',True)])
        self.assertEqual(result['last']['status'],'rejected');alignment.assert_not_called()

    def test_report_records_guide_reason_without_lyric_body(self):
        r=SimpleNamespace(log=StringIO(),write_lock=__import__('threading').Lock())
        s=L.Song('one.mp3','one.mp3',text='private words',timing_note='Supplied line guides retained.')
        Run.record(r,s,status='timed')
        self.assertIn('line guides retained',r.log.getvalue());self.assertNotIn('private words',r.log.getvalue())

    def test_bad_guides_retry_same_words_with_strict_plain_gate(self):
        plain={'lines':10,'lines_aligned':10,'lines_unsure':0,'lines_unheard':0,'words':20}
        with patch.object(T,'belongs',return_value=(True,1.5)), \
             patch.object(T,'process',return_value=(None,[],{'lines':10,'lines_aligned':0},['no alignment fits'])), \
             patch.object(T,'process_plain',return_value=('accepted',[],plain,[])) as retry:
            result=decide.judge({},[('line',LRC,'yours')])
        self.assertEqual(result['timed']['mode'],'plain');self.assertEqual(result['timed']['elrc'],'accepted')
        self.assertEqual(retry.call_args.args[1],'\n'.join(LINES))

    def test_bad_online_guides_cannot_bypass_recording_validation_in_fallback(self):
        with patch.object(T,'belongs',side_effect=[(False,1.0),(None,None)]),patch.object(T,'process_plain') as retry:
            result=decide.judge({},[('line',LRC,'found')])
        self.assertEqual(result['last']['status'],'rejected');retry.assert_not_called()

    def test_guided_low_support_can_be_timed_without_loosening_the_plain_gate(self):
        # Known character positions with low acoustic scores simulate a difficult
        # recording. Line guides make it a local timing task, not global discovery.
        timeline=[];stamps=[]
        for i,line in enumerate(LINES):
            stamps.append(len(timeline)*.02)
            for token in [c for w in line.split() for c in T.word_tokens(w)]:
                row=np.full(31,-15,dtype=np.float32);row[token]=np.log(.08 if i<4 else .8)
                timeline.extend([row.copy() for _ in range(3)])
                blank=np.full(31,-15,dtype=np.float32);blank[0]=np.log(.95);timeline.append(blank)
            blank=np.full(31,-15,dtype=np.float32);blank[0]=np.log(.95);timeline.extend([blank.copy() for _ in range(15)])
        em=np.stack(timeline);duration=len(em)*.02
        feat={'em':em,'emq':em,'env':np.ones(int(duration/.005)+1,dtype=np.float32),'dur':duration,'spf':.02}
        synced=guides.render(list(zip(stamps,LINES)))
        with patch.object(T,'belongs',return_value=(True,1.5)):
            guided=decide.judge(feat,[('line',synced,'yours')])
            plain=decide.judge(feat,[('plain','\n'.join(LINES),'yours')])
        self.assertIsNotNone(guided['timed']);self.assertEqual(guided['timed']['mode'],'line')
        self.assertIsNone(plain['timed']);self.assertIn('unsure',plain['last']['reason'])


class WorkflowModes(unittest.TestCase):
    def test_song_action_two_uses_guides_and_advanced_scratch_is_explicit(self):
        with tempfile.TemporaryDirectory() as d:
            replies=iter(['2','0'])
            m=menu.Menu(d,[],Mock(),reader=lambda _:next(replies),writer=Mock());m.job=Mock()
            s=L.Song('one.mp3','one.mp3')
            m.song_actions([s]);self.assertEqual(m.job.call_args.kwargs['mode_hint'],'current')
            root=Path(d)/'music';root.mkdir();answers=iter(['2','1'])
            m=menu.Menu(str(Path(d)/'app'),[str(root)],Mock(return_value=4),reader=lambda _:next(answers),writer=Mock())
            m.job([L.Song(str(root/'one.mp3'),'one.mp3')],redo=True)
            args=m.execute.call_args.args[0];self.assertEqual(args[args.index('--retime-mode')+1],'unanchored')

    def test_resume_preserves_guided_or_explicit_unanchored_mode_and_exact_scope(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'music';root.mkdir();audio=root/'one.mp3';audio.write_bytes(b'audio');home=Path(d)/'app'
            for mode in ['current','guided','unanchored']:
                with self.subTest(mode=mode):
                    area=Path(library_home(str(home),str(root)))/('run '+mode);area.mkdir(parents=True)
                    data={'schema':1,'source':str(root),'songs':[str(audio)],'options':{'redo':True,'retime_mode':mode,'offline':True},'initial_elrc_hashes':{},'unreadable':[]}
                    (area/'request.json').write_text(json.dumps(data));(area/'outcome.json').write_text(json.dumps({'songs':[{'path':str(audio),'status':'not_timed'}]}))
                    execute=Mock(return_value=4);resume.submit(str(home),str(area),execute)
                    args=execute.call_args.args[0]
                    self.assertEqual(args[args.index('--retime-mode')+1],mode)
                    self.assertEqual(Path(args[args.index('--songs-from')+1]).read_text().splitlines(),[str(audio)])


if __name__=='__main__':unittest.main()
