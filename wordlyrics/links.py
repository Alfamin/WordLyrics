"""Every address WordLyrics downloads from, in one place (the portable Python itself is fetched by WordLyrics.bat).

Each list is tried in order: the normal source first, then the backups. Whatever server a file comes from, it is
used only if its fingerprint matches. All of these were checked to open from test servers inside Iran (2026-10-02).
More addresses can be added without touching the code, in "extra-links.txt" next to WordLyrics.bat.
"""
from __future__ import annotations

import os

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# the Python package index and mirrors of it
INDEXES = [
    "https://pypi.org/simple",
    "https://mirror-pypi.runflare.com/simple",
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
]

# the small script that installs pip
GET_PIP = [
    "https://bootstrap.pypa.io/get-pip.py",
    "https://raw.githubusercontent.com/pypa/get-pip/main/public/get-pip.py",
]

MODEL_LINKS = {
    "mms_fa_300m.onnx": [
        "https://huggingface.co/deskpai/ctc_forced_aligner/resolve/main/04ac86b67129634da93aea76e0147ef3.onnx",
        "https://huggingface.co/niobures/ctc_forced_aligner/resolve/main/04ac86b67129634da93aea76e0147ef3.onnx",
    ],
    "Kim_Vocal_2.onnx": [
        "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/Kim_Vocal_2.onnx",
        "https://huggingface.co/seanghay/uvr_models/resolve/main/Kim_Vocal_2.onnx",
        "https://huggingface.co/Politrees/UVR_resources/resolve/main/models/MDXNet/Kim_Vocal_2.onnx",
    ],
}


def extra(path=None):
    """Lines of extra-links.txt: '<what> <address>' where <what> is 'packages' or a model file name.
    -> {what: [addresses]}; these are tried BEFORE the built-in ones."""
    out = {}
    try:
        with open(path or os.path.join(APP, "extra-links.txt"), encoding="utf-8-sig") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln or ln.startswith("#"):
                    continue
                what, _, url = ln.partition(" ")
                url = url.strip()
                if url.lower().startswith(("https://", "http://")):
                    out.setdefault(what.strip().lower(), []).append(url)
    except OSError:
        pass
    return out


def indexes():
    return _first(extra().get("packages", []), INDEXES)


def model_links(file):
    return _first(extra().get(file.lower(), []), MODEL_LINKS.get(file, []))


def _first(a, b):
    out = []
    for u in list(a) + list(b):
        if u not in out:
            out.append(u)
    return out


def host(url):
    return url.split("//", 1)[-1].split("/", 1)[0]
