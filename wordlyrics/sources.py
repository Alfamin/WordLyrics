"""Genius fallback using public search and lyric pages.

No API keys, cookies, browser sessions, or access-control workarounds. A blocked
provider is skipped.
"""
from __future__ import annotations

from html.parser import HTMLParser
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

ALLOWED_HOSTS = {"genius.com", "www.genius.com"}
LIMIT = 2_000_000
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def settings(path=None):
    config = {}
    if path and os.path.lexists(path):
        try:
            if os.path.islink(path):
                raise ValueError("configuration is a link")
            with open(path,"rb") as f:
                data=f.read(4097)
            if len(data)>4096:
                raise ValueError("configuration is too large")
            raw=json.loads(data.decode("utf-8-sig"))
            if not isinstance(raw,dict):
                raise ValueError("configuration must be an object")
            config = {"genius":raw.get("genius",True)}
        except (OSError, ValueError):
            config["genius"] = False
            config["configuration_error"] = True
    for field in ("genius",):
        if field in config and not isinstance(config[field], bool):
            config[field] = False
            config["configuration_error"] = True
    return config


def safe_url(url):
    try:
        u = urllib.parse.urlsplit(url)
        return u.scheme == "https" and u.hostname in ALLOWED_HOSTS and u.port in (None, 443) and u.username is None and u.password is None
    except (ValueError, TypeError):
        return False


def genius_song_url(url):
    """Canonical public song page only. Drop share tracking; reject credentials and other hosts."""
    if not isinstance(url,str) or any(ord(c)<32 or ord(c)==127 for c in url) or not safe_url(url):
        return ""
    u=urllib.parse.urlsplit(url)
    decoded=urllib.parse.unquote(u.path)
    if re.search(r"%(?![0-9a-fA-F]{2})",u.path) or len(decoded.rstrip("/").split("/")) != 2:
        return ""
    if any(ord(c)<32 or ord(c)==127 for c in decoded):
        return ""
    if u.hostname not in ("genius.com","www.genius.com") or not decoded.rstrip("/").endswith("-lyrics"):
        return ""
    if any(part in (".","..") for part in decoded.split("/")):
        return ""
    return urllib.parse.urlunsplit(("https","genius.com",urllib.parse.quote(u.path.rstrip("/"),safe="/%-._~"),"",""))


class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not safe_url(newurl) or urllib.parse.urlsplit(req.full_url).hostname != urllib.parse.urlsplit(newurl).hostname:
            raise urllib.error.HTTPError(req.full_url, 403, "redirect refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def request(client, provider, url, params=None, *, text=False, stop=None):
    """Bounded, cancellable, short-lived cache. Errors do not echo request URLs or response text."""
    from .fetch import UA
    if provider in client.provider_disabled:
        return "error", client.provider_disabled[provider]
    if stop is not None and stop.is_set():
        return "error", "stopped"
    if not safe_url(url):
        return "error", "unsupported provider address"
    if params:
        url += "?" + urllib.parse.urlencode(sorted(params.items()))
    key = hashlib.sha256((provider + "\0" + url).encode()).hexdigest()
    path = os.path.join(client.cache_dir, provider + "-" + key + ".json")
    try:
        with open(path,"rb") as f:
            raw_cache=f.read(4_000_001)
        if len(raw_cache)>4_000_000:
            raise ValueError("cache is too large")
        cached=json.loads(raw_cache)
        if not isinstance(cached,dict) or cached.get("status") not in ("ok","notfound"):
            raise ValueError("invalid cache object")
        if not client.refresh and time.time() - float(cached.get("ts", 0)) < 86400:
            return cached["status"], cached["data"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    with client.lock:
        delay = max(0.0, .5 - (time.time() - client.last))
        if delay and (stop.wait(delay) if stop is not None else _wait(delay)):
            return "error", "stopped"
        client.last = time.time()
    try:
        hdrs = {"User-Agent": UA, "Accept": "text/html" if text else "application/json"}
        req = urllib.request.Request(url, headers=hdrs)
        opener = urllib.request.build_opener(SameHostRedirect())
        with opener.open(req, timeout=12) as response:
            raw = response.read(LIMIT + 1)
            if len(raw) > LIMIT:
                return "error", "provider response is too large"
            data = raw.decode("utf-8", "replace") if text else json.loads(raw)
        client._remember(path, "ok", data)
        return "ok", data
    except urllib.error.HTTPError as e:
        e.close()
        if e.code == 404:
            client._remember(path, "notfound", None)
            return "notfound", None
        if e.code in (401, 403, 429):
            reason = "access unavailable" if e.code != 429 else "rate limit reached"
            client.provider_disabled[provider] = reason
            return "error", reason
        return "error", "HTTP %d" % e.code
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return "error", "connection or response failed"


def _wait(seconds):
    time.sleep(seconds)
    return False


class GeniusText(HTMLParser):
    """Read only lyrics containers, leaving annotations and other page text out."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.depth, self.parts, self.current = [], None, [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in VOID:
            self.stack.append(tag)
        is_lyrics = attrs.get("data-lyrics-container") == "true" or "lyrics" in (attrs.get("class") or "").split()
        if is_lyrics and self.depth is None:
            self.depth = len(self.stack)
            self.current = []
        elif self.depth is not None and tag in ("br", "p", "div"):
            self.current.append("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag not in self.stack:
            return
        closing = len(self.stack) - 1 - self.stack[::-1].index(tag)
        if self.depth is not None and closing + 1 <= self.depth:
            self.parts.append("".join(self.current))
            self.depth, self.current = None, []
        self.stack = self.stack[:closing]

    def handle_data(self, data):
        if self.depth is not None:
            self.current.append(data)

    def result(self):
        if self.depth is not None:             # incomplete page is not a complete lyric
            return ""
        value = "\n".join(self.parts).replace("\xa0", " ")
        return "\n".join(line.strip() for line in value.splitlines() if line.strip()).strip()


def plain_words(value):
    """Discard ALL provider times. Preserve words and line order for our own model."""
    from . import library as L
    from .timing import HEADING_RE
    out = []
    for line in (value or "").replace("\r\n", "\n").replace("\r", "\n").splitlines():
        if L.META_TAG.match(line):
            continue
        line = re.sub(r"^\s*\[\d+:\d+(?:[.:]\d+)?\]", "", line)
        line = L.WORD_TAG.sub("", line).strip()
        if line and not HEADING_RE.match(line):
            out.append(line)
    return "\n".join(out)


def genius_page(client,song,url,stop=None,*,record=0,title_sim=0.0,seconds_off=0.0,pinned=False):
    from . import fetch as F, library as L
    if client.provider_config.get("genius",True) is False:
        return None,"Genius: disabled in local settings"
    url=genius_song_url(url)
    if not url:
        return None,"Genius: expected a public song lyric page"
    status,page=request(client,"genius",url,text=True,stop=stop)
    if status!="ok":
        return None,"Genius: "+(page or "page unavailable")
    parser=GeniusText()
    try:
        parser.feed(page)
        parser.close()
        text=plain_words(parser.result())
    except (ValueError,TypeError):
        text=""
    if len(text)<40:
        return None,"Genius: complete lyric containers unavailable"
    if F.wants_uncensored(song) and F.censored(text):
        return None,"Genius: censored lyrics were skipped"
    return F.Found(L.PLAIN,text,record,title_sim,seconds_off,provider="genius.com",url=url,
                   require_audio_match=True,pinned=pinned),""


def genius(client, song, stop=None):
    from . import fetch as F, library as L
    if client.provider_config.get("genius", True) is False:
        return None, ""
    queries = [song.artist + " " + title for title in F.title_variants(song.title)[:2]]
    seen, reason = set(), "Genius: no matching complete lyrics"
    for query in queries:
        status, data = request(client, "genius", "https://genius.com/api/search/multi", {"q":query}, stop=stop)
        if status != "ok":
            return None, "Genius: " + (data or "not found")
        if not isinstance(data, dict):
            return None, "Genius: invalid search response"
        response = data.get("response") or {}
        if not isinstance(response, dict):
            return None, "Genius: invalid search response"
        direct=response.get("hits")
        hits = list(direct) if isinstance(direct,list) else []
        sections=response.get("sections")
        for section in sections if isinstance(sections,list) else []:
            if isinstance(section, dict) and section.get("type") == "song":
                entries=section.get("hits")
                if isinstance(entries,list):
                    hits.extend(entries)
        for hit in hits[:12]:
            if not isinstance(hit, dict):
                continue
            c = hit.get("result") or {}
            if not isinstance(c, dict) or not isinstance(c.get("primary_artist") or {}, dict):
                continue
            artist = (c.get("primary_artist") or {}).get("name", "")
            title = c.get("title", "")
            url = c.get("url", "")
            if not safe_url(url) or urllib.parse.urlsplit(url).hostname not in ("genius.com", "www.genius.com"):
                continue
            record=c.get("id")
            if record is not None and not isinstance(record,(int,str)):
                continue
            marker=record if record is not None else url
            if marker in seen or c.get("lyrics_state") not in (None, "complete"):
                continue
            seen.add(marker)
            ok, _, title_sim, off = F.judge(song, artist, title, c.get("duration"),
                                          allow_unknown_duration=True, allow_release_label=True)
            if not ok:
                continue
            result,why=genius_page(client,song,url,stop,record=c.get("id") or 0,title_sim=title_sim,
                                  seconds_off=off if off is not None else 0.0)
            if result is not None:
                return result,""
            reason=why
    return None, reason
