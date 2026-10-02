"""The two model files. They are downloaded once (about 1.3 GB) and checked against known fingerprints.

Where they come from is listed in links.py: the normal address first, then backups. A download that breaks
continues where it stopped, also when the next address takes over (it is the same file everywhere, and the
fingerprint is checked at the end whichever server it came from)."""
from __future__ import annotations

import hashlib
import os
import time
import urllib.request

from . import __version__, links

MODELS = [
    {"file": "mms_fa_300m.onnx", "what": "word aligner (MMS-300m, CC-BY-NC 4.0: personal use)", "size": 1262421764,
     "sha256": "e8bad67fd3533b3d3c145b0ca31bb15383945c13384dd8975baaa7b73f7b61ac"},
    {"file": "Kim_Vocal_2.onnx", "what": "voice isolation (MDX-Net Kim Vocal 2)", "size": 66759214,
     "sha256": "ce74ef3b6a6024ce44211a07be9cf8bc6d87728cc852a68ab34eb8e58cde9c8b"},
]
CHUNK = 1024 * 1024
TRIES_PER_LINK = 3
ROUNDS = 4


class ModelError(Exception):
    pass


def sha256(path, progress=None):
    h, done = hashlib.sha256(), 0
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(4 * CHUNK), b""):
            h.update(b)
            done += len(b)
            if progress:
                progress(done)
    return h.hexdigest()


def missing(folder):
    """Models that are not in the folder yet (a file of the right size is trusted: it was checked when it arrived)."""
    out = []
    for m in MODELS:
        p = os.path.join(folder, m["file"])
        if not (os.path.exists(p) and os.path.getsize(p) == m["size"]):
            out.append(m)
    return out


def _pull(url, part, m, progress, stop):
    """One attempt at one address; appends to what is already there. Raises on any trouble."""
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if have > m["size"]:
        have = 0
    if have == m["size"]:
        return
    req = urllib.request.Request(url, headers={"User-Agent": "WordLyrics/" + __version__})
    if have:
        req.add_header("Range", "bytes=%d-" % have)
    with urllib.request.urlopen(req, timeout=60) as r:
        if have and r.status != 206:
            have = 0                                   # the server started from the beginning
        total = r.headers.get("Content-Length")
        if total and total.isdigit() and int(total) + have != m["size"]:
            raise ModelError("that address has a different file")
        with open(part, "ab" if have else "wb") as fh:
            while True:
                if stop is not None and stop.is_set():
                    raise KeyboardInterrupt
                b = r.read(CHUNK)
                if not b:
                    break
                fh.write(b)
                have += len(b)
                if progress:
                    progress(have, m["size"])
    if have < m["size"]:
        raise ModelError("the connection ended early")


def _sleep(seconds, stop):
    for _ in range(int(seconds * 10)):
        if stop is not None and stop.is_set():
            raise ModelError("stopped")
        time.sleep(0.1)


def download(m, folder, progress=None, stop=None, urls=None, note=None, wait=5, pause=60):
    """Fetch one model into folder. The file only gets its real name after its fingerprint has been checked.
    progress(done_bytes, total_bytes); note(text) says which server is being used and what went wrong.

    Patience: a connection that keeps dropping but gets further each time is simply continued. An address
    is left after TRIES_PER_LINK attempts that brought nothing; when all addresses are through, there is a
    pause (the connection may be down for a moment) and they are tried again, ROUNDS times in all."""
    os.makedirs(folder, exist_ok=True)
    final, part = os.path.join(folder, m["file"]), os.path.join(folder, m["file"] + ".download")
    urls = list(urls or links.model_links(m["file"]))
    first = urls[0] if urls else None
    last = "no download address is known"
    size_now = lambda: os.path.getsize(part) if os.path.exists(part) else 0
    for rnd in range(ROUNDS):
        if not urls:
            break
        if rnd:
            if note:
                note("no address could be reached, trying again in %s" % ("a minute" if pause == 60 else "%d s" % pause))
            _sleep(pause, stop)
        for url in list(urls):
            empty = 0
            while empty < TRIES_PER_LINK:
                if note:
                    note("from %s%s" % (links.host(url), "" if url == first else " (backup address)"))
                before = size_now()
                try:
                    _pull(url, part, m, progress, stop)
                except KeyboardInterrupt:
                    raise ModelError("stopped")
                except Exception as e:
                    last = "%s: %s" % (type(e).__name__, e) if not isinstance(e, ModelError) else str(e)
                    if note:
                        note("%s did not work (%s)" % (links.host(url), last))
                    if isinstance(e, ModelError) and "different file" in str(e):
                        urls.remove(url)                # no point asking this address again
                        break
                    empty = 0 if size_now() - before >= 64 * 1024 else empty + 1
                    _sleep(wait * max(empty, 1), stop)
                    continue
                if note:
                    note("checking the fingerprint")
                if sha256(part) == m["sha256"]:
                    os.replace(part, final)
                    return final
                last = "the file from %s is not the expected one" % links.host(url)
                if note:
                    note(last)
                open(part, "wb").close()                # complete but wrong: start over with an empty file
                urls.remove(url)                        # ... and never ask that address again
                break
    raise ModelError("could not download %s (%s). Check the internet connection and start again: "
                     "it continues where it stopped. Another address can be added in extra-links.txt" % (m["file"], last))
