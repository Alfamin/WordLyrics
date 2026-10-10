"""Safe, precise connectivity diagnoses. Never include URLs, query text, or responses in errors."""
from concurrent.futures import ThreadPoolExecutor
import socket
import ssl
import urllib.error
import urllib.request


def explain(error):
    if isinstance(error, urllib.error.HTTPError):
        code = error.code
        if code in (401, 403):
            return "HTTP_%d" % code, "Access was refused (HTTP %d). The provider or this connection may be blocked." % code
        if code == 429:
            return "RATE_LIMIT", "The provider is limiting requests (HTTP 429). Retry later."
        if code >= 500:
            return "SERVER_ERROR", "The provider has a server error (HTTP %d). Retry later." % code
        return "HTTP_%d" % code, "The provider returned HTTP %d." % code
    reason = getattr(error, "reason", error)
    if isinstance(reason, socket.gaierror):
        return "DNS_FAILED", "The provider name could not be resolved. Check DNS, internet access, or your VPN."
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "TIMEOUT", "The connection timed out. The provider may be blocked or temporarily unavailable."
    if isinstance(reason, ssl.SSLError):
        return "TLS_FAILED", "A secure connection could not be verified. Check your clock, certificates, or connection."
    if isinstance(reason, (ValueError, UnicodeError)):
        return "INVALID_RESPONSE", "The provider sent an unreadable response. A network filter or provider change may be involved."
    return "CONNECTION_FAILED", "The connection failed. Check internet access, your VPN, or proxy."


TARGETS = {
    "LRCLIB": "https://lrclib.net/api/search?q=WordLyrics-connectivity-check",
    "Genius": "https://genius.com/api/search/multi?q=WordLyrics-connectivity-check",
}


def probe(name, opener=None):
    opener = opener or urllib.request.urlopen
    try:
        request = urllib.request.Request(TARGETS[name], headers={"User-Agent": "WordLyrics-connectivity", "Accept": "application/json"})
        with opener(request, timeout=8) as response:
            status = getattr(response, "status", 200)
            response.read(1)
            if status != 200:
                return {"provider": name, "ok": False, "code": "HTTP_%s" % status, "message": "HTTP %s" % status}
        return {"provider": name, "ok": True, "code": "REACHABLE", "message": "Connection works. Lyric availability is checked per song."}
    except urllib.error.HTTPError as e:
        e.close()
        if e.code == 404:
            return {"provider": name, "ok": True, "code": "REACHABLE", "message": "Connection works; this test query had no result."}
        code, message = explain(e)
    except (urllib.error.URLError, OSError, ValueError) as e:
        code, message = explain(e)
    return {"provider": name, "ok": False, "code": code, "message": message}


def check(genius=True):
    names = ["LRCLIB"] + (["Genius"] if genius else [])
    with ThreadPoolExecutor(len(names)) as pool:
        return list(pool.map(probe, names))
