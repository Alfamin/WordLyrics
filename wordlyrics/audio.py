"""Reading songs. Files are only ever opened for reading."""
from __future__ import annotations

import numpy as np

SR = 16000          # what the aligner listens to
SR_SEP = 44100      # what the voice separation listens to
MAX_DAMAGED = 50    # a file with more damaged packets than this (about 1.3 s of mp3) is not used
MAX_MINUTES = 30    # longer files are mixes or albums in one file, not songs


class Unusable(Exception):
    """This file cannot be timed (reason in the message). Never stops the run."""


def decode_tolerant(container, stream):
    """Decoded frames of one audio stream. A damaged packet is not fatal: like a player, skip it, but
    yield the time it covered as a float (seconds) so the caller can keep everything after it in place."""
    import av
    damaged = 0
    for packet in container.demux(stream):
        try:
            yield from packet.decode()
        except av.error.InvalidDataError:
            damaged += 1
            if damaged > MAX_DAMAGED:
                raise
            if packet.duration and packet.time_base:
                yield float(packet.duration * packet.time_base)


def load(path):
    """One read of the file -> (mono 16 kHz float32, stereo 44.1 kHz float32 [2, n])."""
    import av
    try:
        c = av.open(path, mode="r")
    except Exception as e:
        raise Unusable("the audio cannot be opened (%s)" % type(e).__name__) from e
    with c:
        if not c.streams.audio:
            raise Unusable("the file has no audio")
        s = c.streams.audio[0]
        length = float(c.duration) / av.time_base if c.duration else None
        if length and length > MAX_MINUTES * 60:
            raise Unusable("longer than %d minutes" % MAX_MINUTES)
        mono_rs = av.AudioResampler(format="flt", layout="mono", rate=SR)
        st_rs = av.AudioResampler(format="fltp", layout="stereo", rate=SR_SEP)
        mono, stereo, damaged = [], [], False
        try:
            for frame in decode_tolerant(c, s):
                if isinstance(frame, float):               # a damaged packet: silence of the same length
                    mono.append(np.zeros(int(round(frame * SR)), np.float32))
                    stereo.append(np.zeros((2, int(round(frame * SR_SEP))), np.float32))
                    damaged = True
                    continue
                for f in mono_rs.resample(frame):
                    mono.append(f.to_ndarray().reshape(-1))
                for f in st_rs.resample(frame):
                    stereo.append(f.to_ndarray())
            for f in mono_rs.resample(None):
                mono.append(f.to_ndarray().reshape(-1))
            for f in st_rs.resample(None):
                stereo.append(f.to_ndarray())
        except av.error.InvalidDataError as e:
            raise Unusable("the audio is too damaged to read") from e
        if not mono:
            raise Unusable("the file has no audio")
        wav = np.concatenate(mono).astype(np.float32)
        if len(wav) < SR:
            raise Unusable("shorter than one second")
        if damaged and length and abs(len(wav) / SR - length) > 1.0:
            # after repairing, the length does not add up: the timing cannot be trusted, so do not use the file
            raise Unusable("damaged file: %.0f s readable of %.0f s" % (len(wav) / SR, length))
    return wav, np.concatenate(stereo, axis=1).astype(np.float32)


def to_mono_16k(x):
    """Stereo 44.1 kHz [2, n] -> mono 16 kHz."""
    import av
    rs = av.AudioResampler(format="flt", layout="mono", rate=SR)
    out, step = [], SR_SEP * 10
    for i in range(0, x.shape[1], step):
        fr = av.AudioFrame.from_ndarray(np.ascontiguousarray(x[:, i:i + step]), format="fltp", layout="stereo")
        fr.sample_rate = SR_SEP
        out += [f.to_ndarray().reshape(-1) for f in rs.resample(fr)]
    out += [f.to_ndarray().reshape(-1) for f in rs.resample(None)]
    return np.concatenate(out).astype(np.float32)


def probe(path):
    """Length in seconds and the container's own tags, for files the tag reader cannot handle.
    -> (seconds | None, {lower-case key: text})"""
    import av
    try:
        with av.open(path, mode="r") as c:
            meta = {str(k).lower(): str(v) for k, v in (c.metadata or {}).items()}
            if c.streams.audio:
                meta.update({str(k).lower(): str(v) for k, v in (c.streams.audio[0].metadata or {}).items() if str(k).lower() not in meta})
            return (float(c.duration) / av.time_base if c.duration else None), meta
    except Exception:
        return None, {}
