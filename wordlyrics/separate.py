"""Isolating the voice with an MDX-Net model (Kim_Vocal_2), numpy + onnxruntime only.

Settings come from the model's published data: n_fft 7680, 3072 frequency bins, 256 frames, hop 1024,
compensate 1.009. The work is cut into pieces so that the part before the model (CPU), the model
(GPU) and the part after it (CPU) can run side by side.
"""
import numpy as np

N_FFT, HOP, DIM_F, DIM_T, COMP = 7680, 1024, 3072, 256, 1.009
CHUNK = HOP * (DIM_T - 1)
TRIM = N_FFT // 2
GEN = CHUNK - 2 * TRIM
WIN = np.hanning(N_FFT + 1)[:-1].astype(np.float32)
BATCH = 4


def _stft(w):  # w: [b, 2, CHUNK] -> [b, 4, DIM_F, DIM_T]
    b = w.shape[0]
    xp = np.pad(w.reshape(b * 2, CHUNK), ((0, 0), (TRIM, TRIM)), mode="reflect")
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(DIM_T)[:, None]
    spec = np.fft.rfft(xp[:, idx] * WIN, axis=-1)[:, :, :DIM_F]            # [b*2, T, F]
    spec = spec.transpose(0, 2, 1).reshape(b, 2, DIM_F, DIM_T)
    return np.stack([spec.real, spec.imag], axis=2).reshape(b, 4, DIM_F, DIM_T).astype(np.float32)


def _istft(s):  # s: [b, 4, DIM_F, DIM_T] -> [b, 2, CHUNK]
    b = s.shape[0]
    s = s.reshape(b, 2, 2, DIM_F, DIM_T)
    spec = np.zeros((b * 2, DIM_T, N_FFT // 2 + 1), np.complex64)
    spec[:, :, :DIM_F] = (s[:, :, 0] + 1j * s[:, :, 1]).reshape(b * 2, DIM_F, DIM_T).transpose(0, 2, 1)
    frames = np.fft.irfft(spec, n=N_FFT, axis=-1).astype(np.float32) * WIN
    n = CHUNK + 2 * TRIM
    out = np.zeros((b * 2, n), np.float32)
    norm = np.zeros(n, np.float32)
    for t in range(DIM_T):
        out[:, t * HOP: t * HOP + N_FFT] += frames[:, t]
        norm[t * HOP: t * HOP + N_FFT] += WIN ** 2
    out /= np.maximum(norm, 1e-8)
    return out[:, TRIM: TRIM + CHUNK].reshape(b, 2, CHUNK)


def count_batches(n, batch=BATCH):
    """How many model inputs a song of n samples (44.1 kHz) is cut into."""
    pad = GEN - n % GEN
    return -(-len(range(0, n + pad, GEN)) // batch)


def spec_batches(mix, batch=BATCH):
    """Everything before the model: stereo mix [2, n] at 44.1 kHz -> the model's inputs, one batch at a time."""
    n = mix.shape[1]
    pad = GEN - n % GEN
    mp = np.concatenate([np.zeros((2, TRIM), np.float32), mix, np.zeros((2, pad + TRIM), np.float32)], axis=1)
    starts = list(range(0, n + pad, GEN))
    for i in range(0, len(starts), batch):
        spec = _stft(np.stack([mp[:, s: s + CHUNK] for s in starts[i: i + batch]]))
        spec[:, :, :3, :] = 0
        yield spec


def back(res):
    """Everything after the model, for one batch of its output."""
    return _istft(res)[:, :, TRIM: CHUNK - TRIM]


def join(parts, n):
    """The pieces in order -> isolated voice [2, n] at 44.1 kHz."""
    return np.concatenate(parts, axis=0).transpose(1, 0, 2).reshape(2, -1)[:, :n] * COMP
