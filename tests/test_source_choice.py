"""Explicit source choice corrects search ambiguity but cannot disable safety checks."""
from contextlib import nullcontext,redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wordlyrics import __main__ as cli,fetch as F,library as L,sources as S
from wordlyrics.pipeline import Run

URL="https://genius.com/Example-song-lyrics"
TEXT="We follow the morning light across the empty city\nWe sing another gentle line beneath the open sky"


class SourceChoiceTests(unittest.TestCase):
    def song(self,**kwargs):
        values=dict(path="example.flac",rel="example.flac",title="Uploader title",artist="Uploader",seconds=200,lyrics_url=URL)
        values.update(kwargs)
        return L.Song(**values)

    def test_source_urls_cannot_point_to_other_hosts_or_credentials(self):
        for url in ("http://genius.com/a-lyrics","https://evil.example/a-lyrics","https://genius.com.evil.example/a-lyrics",
                    "https://user:password@genius.com/a-lyrics","https://genius.com:8443/a-lyrics","https://genius.com/../a-lyrics",
                    "https://genius.com/a%0a-lyrics","https://genius.com/a%zz-lyrics","https://genius.com/a/b-lyrics"):
            self.assertEqual(S.genius_song_url(url),"")

    def test_share_tracking_is_not_sent_or_stored(self):
        self.assertEqual(S.genius_song_url(URL+"?utm_source=share#note-123"),URL)

    def test_chosen_source_skips_search_but_requires_audio_match(self):
        html='<div data-lyrics-container="true">'+TEXT.replace("\n","<br>")+'</div>'
        with tempfile.TemporaryDirectory() as d,patch.object(S,"request",return_value=("ok",html)) as req:
            c=F.Client(d,provider_config={})
            c.get=Mock()
            result,_=F.lookup(c,self.song())
            self.assertTrue(result.pinned)
            self.assertTrue(result.require_audio_match)
            c.get.assert_not_called()
            self.assertEqual(req.call_args.args[2],URL)

    def test_failed_chosen_source_does_not_silently_switch_to_other_words(self):
        with tempfile.TemporaryDirectory() as d,patch.object(S,"request") as req:
            c=F.Client(d,provider_config={})
            c.get=Mock()
            result,note=F.lookup(c,self.song(),skip=["genius.com"])
            self.assertIsNone(result)
            self.assertIn("chosen",note)
            c.get.assert_not_called()
            req.assert_not_called()

    def test_a_chosen_page_does_not_allow_censored_words(self):
        html='<div data-lyrics-container="true">'+TEXT.replace("morning","m***ing")+'</div>'
        with tempfile.TemporaryDirectory() as d,patch.object(S,"request",return_value=("ok",html)):
            result,note=F.lookup(F.Client(d,provider_config={}),self.song(explicit=True))
            self.assertIsNone(result)
            self.assertIn("censored",note)

    def test_existing_line_guides_do_not_override_the_chosen_page(self):
        s=self.song(tier=L.LINE,text="[00:01.00]old words",found=F.Found(L.PLAIN,TEXT,pinned=True))
        self.assertEqual(Run.attempts(object(),s),[("plain",TEXT,"found",True)])

    def test_choice_command_creates_only_a_new_source_file(self):
        with tempfile.TemporaryDirectory() as d:
            audio=Path(d)/"example.flac"
            audio.write_bytes(b"unchanged audio")
            with redirect_stdout(StringIO()):
                self.assertEqual(cli.main(["source",str(audio),URL]),0)
                self.assertEqual(cli.main(["source",str(audio),"https://genius.com/Other-song-lyrics"]),2)
            self.assertEqual(audio.read_bytes(),b"unchanged audio")
            self.assertEqual(audio.with_suffix(".lyrics-source.txt").read_text().strip(),URL)

    def test_source_file_is_read_without_network_calls(self):
        with tempfile.TemporaryDirectory() as d:
            audio=Path(d)/"example.flac"
            audio.write_bytes(b"audio")
            audio.with_suffix(".lyrics-source.txt").write_text(URL,encoding="utf-8")
            info=dict(seconds=200,artist="Uploader",title="Uploader title")
            with patch.object(L,"embedded",return_value=(info,[])):
                s=L.read_song(str(audio),audio.name,audio.stat().st_size)
            self.assertEqual(s.lyrics_url,URL)
            self.assertTrue(F.needs_lookup(s))

    def test_retry_bypasses_a_successful_cache_response(self):
        response=Mock()
        response.read.return_value=json.dumps({"id":2}).encode()
        with tempfile.TemporaryDirectory() as d:
            c=F.Client(d,provider_config={},refresh=True)
            params={"track_name":"example"}
            cache=c._cache(F.API+"/get?track_name=example")
            c._remember(cache,"ok",{"id":1})
            with patch.object(F.urllib.request,"urlopen",return_value=nullcontext(response)) as opened:
                status,data=c.get("/get",params)
            self.assertEqual(data["id"],2)
            opened.assert_called_once()

    def test_retry_bypasses_genius_cache_too(self):
        response=Mock()
        response.read.return_value=b'{"response":{"new":true}}'
        opener=Mock()
        opener.open.return_value=nullcontext(response)
        with tempfile.TemporaryDirectory() as d,patch.object(S.urllib.request,"build_opener",return_value=opener):
            c=F.Client(d,provider_config={},refresh=True)
            import hashlib
            key=hashlib.sha256(("genius\0"+URL).encode()).hexdigest()
            c._remember(str(Path(d)/("genius-"+key+".json")),"ok",{"old":True})
            self.assertEqual(S.request(c,"genius",URL)[1],{"response":{"new":True}})
            opener.open.assert_called_once()

    def test_corrupt_cache_does_not_stop_fetching(self):
        response=Mock()
        response.read.return_value=b'{"response":{"hits":[]}}'
        opener=Mock()
        opener.open.return_value=nullcontext(response)
        with tempfile.TemporaryDirectory() as d,patch.object(S.urllib.request,"build_opener",return_value=opener):
            c=F.Client(d,provider_config={})
            import hashlib
            key=hashlib.sha256(("genius\0"+URL).encode()).hexdigest()
            (Path(d)/("genius-"+key+".json")).write_text("[]")
            self.assertEqual(S.request(c,"genius",URL)[0],"ok")

    def test_bad_settings_disable_genius_instead_of_enabling_it(self):
        for text in ("[]","invalid JSON", "x"*4097):
            with tempfile.TemporaryDirectory() as d:
                path=Path(d)/"lyrics-providers.json"
                path.write_text(text)
                settings=S.settings(path)
                self.assertFalse(settings["genius"])
                self.assertTrue(settings["configuration_error"])

    def test_invalid_source_file_leaves_the_song_alone(self):
        with tempfile.TemporaryDirectory() as d:
            audio=Path(d)/"example.flac"
            audio.write_bytes(b"audio")
            audio.with_suffix(".lyrics-source.txt").write_text("https://evil.example/a-lyrics")
            with patch.object(L,"embedded",return_value=({"seconds":200,"artist":"Artist","title":"Song"},[])):
                self.assertIn("invalid lyrics-source",L.read_song(str(audio),audio.name,5).skip)


if __name__=="__main__":
    unittest.main()
