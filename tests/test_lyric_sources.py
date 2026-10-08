"""Provider, censorship, and pipeline behavior using synthetic lyrics and fake responses."""
import json
from contextlib import nullcontext
from pathlib import Path
import queue
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wordlyrics import decide, fetch as F, library as L, sources as S
from wordlyrics.pipeline import Run

PLAIN = "We follow the morning light across the empty city\nWe sing another gentle line beneath the open sky"
LINE = "\n".join(f"[00:{i*3:02d}.00]We follow the morning light across the empty city" for i in range(6))


def song(**kwargs):
    params = dict(path="example.flac", rel="example.flac", artist="Example Artist", title="Example Song", seconds=200)
    params.update(kwargs)
    return L.Song(**params)


def lrclib(**kwargs):
    result = dict(id=12, trackName="Example Song", artistName="Example Artist", duration=200,
                  plainLyrics=PLAIN, syncedLyrics=LINE, instrumental=False)
    result.update(kwargs)
    return result


def client(directory):
    return F.Client(str(directory), provider_config={})


class MatchingTests(unittest.TestCase):
    def test_masks_trigger_uncensored_plain_instead_of_synced_text(self):
        result = F._usable(lrclib(syncedLyrics=LINE.replace("morning", "m***ing")), song())
        self.assertEqual(result.tier, L.PLAIN)
        self.assertEqual(result.text, PLAIN)

    def test_missing_words_are_not_invented(self):
        c = lrclib(plainLyrics=PLAIN.replace("morning", "m***ing"), syncedLyrics=LINE.replace("morning", "m***ing"))
        self.assertIsNone(F._usable(c, song()))

    def test_clean_version_does_not_match_unknown_or_explicit_recording(self):
        for explicit in (None, True):
            self.assertFalse(F.judge(song(explicit=explicit), "Example Artist", "Example Song (Clean)", 200)[0])

    def test_known_clean_recording_can_keep_masked_words(self):
        result = F._usable(lrclib(syncedLyrics=LINE.replace("morning", "m***ing")), song(explicit=False))
        self.assertEqual(result.tier, L.LINE)
        self.assertTrue(F.judge(song(explicit=False), "Example Artist", "Example Song (Clean)", 200)[0])

    def test_clean_in_a_song_name_is_not_a_clean_version_advisory(self):
        self.assertTrue(F.wants_uncensored(song(title="Clean Slate")))
        self.assertTrue(F.wants_uncensored(song(title="Clean Slate",explicit=True)))
        self.assertFalse(F.wants_uncensored(song(title="Example Song (Clean)")))

    def test_mixed_word_timestamps_become_line_guides(self):
        result = F._usable(lrclib(syncedLyrics="\n".join(f"[00:{i*3:02d}.00]<00:{i*3:02d}.00>Morning<00:{i*3+1:02d}.00> light" for i in range(6))), song())
        self.assertEqual(result.tier, L.LINE)
        self.assertFalse(L.WORD_TAG.search(result.text))

    def test_unknown_duration_only_allowed_for_verified_fallback(self):
        self.assertFalse(F.judge(song(), "Example Artist", "Example Song", None)[0])
        self.assertTrue(F.judge(song(), "Example Artist", "Example Song", None, allow_unknown_duration=True)[0])
        self.assertFalse(F.judge(song(), "Example Artist", "Example Song", "garbage", allow_unknown_duration=True)[0])

    def test_unreleased_label_allowed_but_remix_is_not(self):
        s = song(title="Example Song (Unreleased)")
        self.assertTrue(F.judge(s, "Example Artist", "Example Song", None, allow_unknown_duration=True, allow_release_label=True)[0])
        self.assertFalse(F.judge(s, "Example Artist", "Example Song (Remix)", None, allow_unknown_duration=True, allow_release_label=True)[0])

    def test_numbered_takes_are_not_mistaken_for_the_original(self):
        self.assertFalse(F.judge(song(title="Example Song (V2)"), "Example Artist", "Example Song", 200)[0])

    def test_malformed_online_metadata_is_refused(self):
        self.assertFalse(F.judge(song(), [], {}, float("nan"), allow_unknown_duration=True)[0])
        self.assertIsNone(F._usable(lrclib(plainLyrics={},syncedLyrics=[]), song()))

    def test_censored_line_source_needs_lookup(self):
        self.assertTrue(F.needs_lookup(song(tier=L.LINE, text=LINE.replace("morning", "m***ing"))))
        self.assertFalse(F.needs_lookup(song(tier=L.LINE, text=LINE)))

    def test_advisory_is_not_a_generic_star_rating(self):
        self.assertIs(L.advisory_value(4, itunes=True), True)
        self.assertIs(L.advisory_value(2, itunes=True), False)
        self.assertIsNone(L.advisory_value(0, itunes=True))
        self.assertIsNone(L.advisory_value(5))


class ProviderTests(unittest.TestCase):
    def test_provider_configuration_does_not_import_credentials(self):
        with patch.dict(S.os.environ,{"GENIUS_ACCESS_TOKEN":"synthetic-private-key"}):
            self.assertNotIn("genius_access_token",S.settings())
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"config.json"
            path.write_text(json.dumps({"genius":True,"genius_access_token":"synthetic-private-key"}))
            self.assertEqual(S.settings(path),{"genius":True})

    def test_key_is_not_forwarded_to_another_host_on_redirect(self):
        req = S.urllib.request.Request("https://api.genius.com/search", headers={"Authorization":"Bearer synthetic-private-key"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            S.SameHostRedirect().redirect_request(req, None, 302, "redirect", {}, "https://example.invalid/search")
        caught.exception.close()

    def test_genius_parser_keeps_words_not_annotations(self):
        parser = S.GeniusText()
        parser.feed('<h1>Other page text</h1><div data-lyrics-container="true">[Verse 1]<br/>We <a>follow</a> the light<br>Another line</div><div>Annotation</div><div data-lyrics-container="true">Last line</div>')
        self.assertEqual(S.plain_words(parser.result()), "We follow the light\nAnother line\nLast line")

    def test_incomplete_genius_page_is_not_used(self):
        parser = S.GeniusText()
        parser.feed('<div data-lyrics-container="true">Some truncated lyric')
        self.assertEqual(parser.result(), "")

    def test_boolean_html_class_does_not_crash_fetching(self):
        parser=S.GeniusText()
        parser.feed('<div class>Other text</div><div data-lyrics-container="true">'+PLAIN+'</div>')
        self.assertEqual(parser.result(),PLAIN)

    def test_incomplete_genius_search_result_is_not_used(self):
        response = {"response":{"hits":[{"result":{"id":1,"lyrics_state":"incomplete","title":"Example Song", "primary_artist":{"name":"Example Artist"},"url":"https://genius.com/Example-song-lyrics"}}]}}
        with tempfile.TemporaryDirectory() as d, patch.object(S,"request",return_value=("ok",response)) as req:
            self.assertIsNone(S.genius(client(d),song())[0])
            self.assertTrue(all("/api/search/" in call.args[2] for call in req.call_args_list))

    def test_all_supplier_times_are_removed(self):
        self.assertEqual(S.plain_words("[ar:Example]\n[00:01.20]<00:01.20>Hello<00:01.70> there\n[Verse 2]"), "Hello there")

    def test_lrc_priority_avoids_extra_requests(self):
        with tempfile.TemporaryDirectory() as d, patch.object(S, "genius") as genius:
            c = client(d)
            c.get = Mock(return_value=("ok", lrclib()))
            result, note = F.lookup(c, song())
            self.assertEqual(result.provider, "lrclib.net")
            genius.assert_not_called()

    def test_censored_exact_result_searches_for_uncensored_record(self):
        masked = lrclib(syncedLyrics=LINE.replace("morning", "m***ing"), plainLyrics=PLAIN.replace("morning", "m***ing"))
        with tempfile.TemporaryDirectory() as d:
            c = client(d)
            c.get = Mock(side_effect=[("ok", masked), ("ok", [masked, lrclib(id=13)])])
            result, _ = F.lookup(c, song())
            self.assertEqual(result.record, 13)
            self.assertEqual([call.args[0] for call in c.get.call_args_list], ["/get", "/search"])

    def test_genius_fallback_is_plain_and_requires_audio_match(self):
        response = {"response":{"sections":[{"type":"song", "hits":[{"result":{"id":1, "title":"Example Song", "primary_artist":{"name":"Example Artist"}, "lyrics_state":"complete", "url":"https://genius.com/Example-song-lyrics"}}]}]}}
        html = '<div data-lyrics-container="true">' + PLAIN.replace("\n", "<br>") + '</div>'
        with tempfile.TemporaryDirectory() as d, patch.object(S, "request", side_effect=[("ok", response), ("ok", html)]):
            result, _ = S.genius(client(d), song())
            self.assertEqual(result.provider, "genius.com")
            self.assertEqual(result.tier, L.PLAIN)
            self.assertTrue(result.require_audio_match)

    def test_external_song_url_is_not_fetched(self):
        response = {"response":{"hits":[{"result":{"id":1, "title":"Example Song", "primary_artist":{"name":"Example Artist"}, "url":"https://example.invalid/lyrics"}}]}}
        with tempfile.TemporaryDirectory() as d, patch.object(S, "request", return_value=("ok", response)) as req:
            result, _ = S.genius(client(d), song())
            self.assertIsNone(result)
            self.assertTrue(all(call.args[2].startswith("https://genius.com/api/") for call in req.call_args_list))

    def test_access_block_does_not_repeat_for_each_song(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError("https://genius.com/api/search/multi", 403, "blocked", {}, None)
        with tempfile.TemporaryDirectory() as d, patch.object(S.urllib.request, "build_opener", return_value=opener):
            c = client(d)
            for _ in range(2):
                status, _ = S.request(c, "genius", "https://genius.com/api/search/multi")
                self.assertEqual(status, "error")
            self.assertEqual(opener.open.call_count, 1)


class PipelineTests(unittest.TestCase):
    def test_offline_fetch_never_constructs_network_client(self):
        run = object.__new__(Run)
        run.opts = Mock(offline=True)
        run.fetch_done = threading.Event()
        run.undecided = 0
        run.enqueue = Mock()
        run.no_lyrics = Mock()
        ready = queue.Queue()
        with patch.object(F, "Client") as ctor:
            run.fetcher(Mock(), [song(tier=L.PLAIN, text=PLAIN)], ready)
            ctor.assert_not_called()
        run.enqueue.assert_called_once()
        self.assertTrue(run.fetch_done.is_set())

    def test_unverified_fallback_is_rejected_even_if_quality_would_pass(self):
        with patch.object(decide.T, "belongs", return_value=(None,1)), patch.object(decide.T, "process_plain") as timed:
            result = decide.judge({}, [("plain",PLAIN,"found",True)])
            self.assertIsNone(result["timed"])
            self.assertEqual(result["last"]["status"], "rejected")
            timed.assert_not_called()

    def test_existing_plain_text_can_try_genius_before_its_own_text(self):
        s = song(tier=L.PLAIN, text=PLAIN, found=F.Found(L.PLAIN, PLAIN, provider="genius.com", require_audio_match=True))
        attempts = Run.attempts(object(), s)
        self.assertEqual(attempts[0][2:], ("found",True))
        self.assertEqual(attempts[1][2], "yours")

    def test_censored_input_is_not_used_when_fallback_fails(self):
        s = song(tier=L.LINE, text=LINE.replace("morning", "m***ing"))
        self.assertEqual(Run.attempts(object(), s), [])

    def test_quality_failure_retries_source_with_same_features(self):
        run = object.__new__(Run)
        run.lyrics_client = object()
        run.opts = Mock(offline=False)
        run.stop = threading.Event()
        run._apply = Mock()
        run.write_errors = 0
        s = song(tier=L.NONE, found=F.Found(L.LINE, LINE))
        failed = {"timed":None, "keep_lines":None, "last":{"lyrics":"found","status":"rejected"}}
        fut = Mock()
        fut.result.return_value = failed
        alternative = F.Found(L.PLAIN, PLAIN, provider="genius.com", require_audio_match=True)
        success = {"timed":{"status":"timed"},"keep_lines":None,"last":None}
        feat = {"already_heard":True}
        with patch.object(F, "lookup", return_value=(alternative,"")) as lookup, patch.object(decide, "judge", return_value=success) as judge:
            run.apply(s, fut, feat)
            self.assertIs(judge.call_args.args[0], feat)
            self.assertEqual(lookup.call_args.kwargs["skip"], ["lrclib.net"])
            run._apply.assert_called_once_with(s, success)


if __name__ == "__main__":
    unittest.main()
