"""The two listening models and the choice of where to run them (graphics card or processor).

Only ONE listener runs at a time. Running several at once was measured to be slower on a strong
graphics card and to exhaust memory on a weaker one; what makes it fast instead is letting helper
threads prepare the next piece of work while the graphics card is busy (see pipeline.py).
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import time

import numpy as np

from . import separate as S
from .audio import SR

ALIGNER, SEPARATOR = "mms_fa_300m.onnx", "Kim_Vocal_2.onnx"
WINDOW_S, CONTEXT_S = 30, 2
RETRY_AFTER = (120, 300, 900, 1800)   # seconds until the graphics card is tried again after it failed (the last wait then repeats)


def gpu_name():
    """Name of the graphics card, for display only."""
    if platform.system() != "Windows":
        return ""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                              "(Get-CimInstance Win32_VideoController | Sort-Object AdapterRAM -Descending | Select-Object -First 1).Name"],
                             capture_output=True, text=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        return out.splitlines()[0].strip() if out else ""
    except Exception:
        return ""


def lift_quiet(voc, win_s=1.5, max_gain=8.0):
    """Slow automatic gain on the isolated voice: quiet phrases are brought up towards the loud ones."""
    hop = int(0.05 * SR)
    n = len(voc) // hop
    if n == 0:
        return voc.astype(np.float32)
    rms = np.sqrt((voc[: n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(axis=1))
    k = max(1, min(int(win_s / 0.05), n))                   # a clip shorter than the window: np.convolve would return more values than there are steps
    level = np.sqrt(np.convolve(rms ** 2, np.ones(k) / k, mode="same"))
    target = np.percentile(level, 90)
    floor = 0.03 * target                                   # below this it is silence: do not amplify noise
    gain = np.where(level > floor, np.minimum(target / np.maximum(level, 1e-9), max_gain), 1.0)
    g = np.interp(np.arange(len(voc)), (np.arange(n) + 0.5) * hop, gain)
    return (voc * g).astype(np.float32)


class Engine:
    """device: 'auto' (measure and take the faster one for each model), 'gpu' or 'cpu'."""

    changed = None           # the run puts a function here; it is told when a model moves between graphics card and processor
    gpu_lost = None          # model -> how many times the graphics card failed on it
    gpu_retry = None         # model -> when (time.monotonic) the graphics card is tried again; only while the processor stands in

    def __init__(self, models_dir, device="auto", settings_path=None, say=lambda msg: None):
        import onnxruntime as ort
        ort.set_default_logger_severity(3)
        self.ort = ort
        self.dir = models_dir
        self.say = say
        self.sessions = {}                   # (model, "gpu"|"cpu") -> session
        self.gpu_possible = "DmlExecutionProvider" in ort.get_available_providers() or \
                            "CUDAExecutionProvider" in ort.get_available_providers()
        self.gpu = gpu_name() if self.gpu_possible else ""
        self.where = {ALIGNER: "cpu", SEPARATOR: "cpu"}
        self.gpu_failures = 0
        self.gpu_lost, self.gpu_retry = {}, {}
        self.pace = 1.0                      # share of the time the models may work (set by the speed choice)
        self.paused = False
        self.stop = None                     # threading.Event of the run, so a pause never outlives a stop
        self.speed = {}
        self.note = ""
        if device == "cpu" or not self.gpu_possible:
            if device == "gpu":
                self.note = "no usable graphics card was found, using the processor"
        elif device == "gpu":
            self._try_gpu(ALIGNER), self._try_gpu(SEPARATOR)
        else:
            self._auto(settings_path)
        for m in (ALIGNER, SEPARATOR):
            self._session(m, self.where[m])

    # ---------------------------------------------------------------- sessions
    def _session(self, model, where):
        key = (model, where)
        if key not in self.sessions:
            path = os.path.join(self.dir, model)
            if where == "cpu":
                self.sessions[key] = self.ort.InferenceSession(path, providers=["CPUExecutionProvider"])
            else:
                avail = self.ort.get_available_providers()
                if "DmlExecutionProvider" in avail:
                    try:        # on a laptop with two graphics chips: take the strong one
                        s = self.ort.InferenceSession(path, providers=[("DmlExecutionProvider", {"performance_preference": "high_performance"}),
                                                                       "CPUExecutionProvider"])
                    except Exception:
                        s = self.ort.InferenceSession(path, providers=["DmlExecutionProvider", "CPUExecutionProvider"])
                else:
                    s = self.ort.InferenceSession(path, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
                if s.get_providers()[0] == "CPUExecutionProvider":
                    raise RuntimeError("the graphics card was not accepted")
                self.sessions[key] = s
        return self.sessions[key]

    def _drop(self, model, where):
        self.sessions.pop((model, where), None)

    def _try_gpu(self, model):
        try:
            self._session(model, "gpu")
            self._bench(model, "gpu")            # one real call: proves it works and warms it up
            self.where[model] = "gpu"
        except Exception:
            self._drop(model, "gpu")
            self.note = "the graphics card could not run the models, using the processor"

    def _bench(self, model, where):
        """Seconds for one typical call."""
        s = self._session(model, where)
        if model == ALIGNER:
            x = (np.random.default_rng(0).standard_normal((1, (WINDOW_S + 2 * CONTEXT_S) * SR)) * 0.05).astype(np.float32)
            feed, outs = {"input_values": x}, ["logits"]
        else:
            x = (np.random.default_rng(0).standard_normal((S.BATCH, 4, S.DIM_F, S.DIM_T)) * 0.05).astype(np.float32)
            feed, outs = {s.get_inputs()[0].name: x}, None
        t = time.perf_counter()
        s.run(outs, feed)
        return time.perf_counter() - t

    def _auto(self, settings_path):
        """Measure one typical call of each model on the graphics card and on the processor, keep the
        faster one. The result is remembered so later runs start at once."""
        sig = "%s|%s|%s|%s" % (self.ort.__version__, self.gpu, os.cpu_count(), platform.machine())
        saved = {}
        if settings_path and os.path.exists(settings_path):
            try:
                saved = json.load(open(settings_path, encoding="utf-8"))
            except Exception:
                saved = {}
        locations=saved.get("where") if isinstance(saved,dict) else None
        if not isinstance(locations,dict) or any(value not in ("cpu","gpu") for value in locations.values()):saved={}
        if saved.get("signature") == sig and set(saved.get("where", {})) == {ALIGNER, SEPARATOR}:
            self.where = dict(saved["where"])
            self.speed = saved.get("seconds", {})
            for m in (ALIGNER, SEPARATOR):
                if self.where[m] == "gpu":
                    try:
                        self._session(m, "gpu")
                    except Exception:
                        self.where[m] = "cpu"
            return
        self.say("Testing how fast this computer runs the models (once, about half a minute)")
        self.speed = {}
        for m in (ALIGNER, SEPARATOR):
            t_gpu = None
            try:
                self._bench(m, "gpu")                    # first call includes start-up work
                t_gpu = min(self._bench(m, "gpu"), self._bench(m, "gpu"))
            except Exception:
                self._drop(m, "gpu")
            try:t_cpu = self._bench(m, "cpu")
            except Exception:
                if t_gpu is None:raise
                t_cpu=None
                self.note="CPU_UNAVAILABLE: the graphics card passed its test; using it for this model."
                self.say(self.note)
            self.speed[m] = {"gpu": t_gpu, "cpu": t_cpu}
            self.where[m] = "gpu" if t_gpu is not None and (t_cpu is None or t_gpu < t_cpu) else "cpu"
            self._drop(m, "cpu" if self.where[m] == "gpu" else "gpu")
        if settings_path:
            try:
                with open(settings_path, "w", encoding="utf-8") as fh:
                    json.dump({"signature": sig, "where": self.where, "seconds": self.speed}, fh, indent=1)
            except OSError:
                pass

    def describe(self):
        a, s = self.where[ALIGNER], self.where[SEPARATOR]
        if a == s == "gpu":
            return "graphics card" + (" (%s)" % self.gpu if self.gpu else "")
        if a == s == "cpu":
            return "processor (%d threads)" % (os.cpu_count() or 1) + (" - " + self.note if self.note else "")
        if self.gpu_retry:
            return "graphics card + processor (the graphics card failed on one model; it is tried again later)"
        return "graphics card + processor (each model where it is faster)"

    # ---------------------------------------------------------------- running
    def _lost_gpu(self, model):
        """The graphics card failed on this model: remember it, and when it is tried again (later each time)."""
        if self.gpu_lost is None:
            self.gpu_lost, self.gpu_retry = {}, {}
        n = self.gpu_lost[model] = self.gpu_lost.get(model, 0) + 1
        self.gpu_retry[model] = time.monotonic() + RETRY_AFTER[min(n, len(RETRY_AFTER)) - 1]

    def _gpu_again(self, model):
        """The processor is standing in for a failed graphics card: when it is time, see whether the card works
        again. One real call proves it. Still the one listener: this runs between two model calls."""
        if time.monotonic() < self.gpu_retry[model]:
            return
        try:
            self._session(model, "gpu")
            self._bench(model, "gpu")
        except Exception:
            self._drop(model, "gpu")
            self._lost_gpu(model)                 # still not working: wait longer
            return
        del self.gpu_retry[model]
        self.where[model] = "gpu"
        self._drop(model, "cpu")
        if not self.gpu_retry and self.note.startswith("GPU_FALLBACK"):
            self.note = ""
        self.say("The graphics card works again; going back to it")
        if self.changed:
            self.changed("")

    def _run(self, model, outs, feed):
        """Run one model call. If the graphics card fails (usually out of memory), carry on with the processor
        and try the card again later."""
        if self.gpu_retry and model in self.gpu_retry:
            self._gpu_again(model)
        where = self.where[model]
        try:
            t = time.perf_counter()
            out = self._session(model, where).run(outs, feed)
            self._rest(time.perf_counter() - t)
            return out
        except Exception:
            if where != "gpu":
                raise
            self.gpu_failures += 1
            self.where[model] = "cpu"
            self._drop(model, "gpu")
            self._lost_gpu(model)
            self.note = "GPU_FALLBACK: the graphics card failed; continuing on the processor"
            self.say(self.note)
            if self.changed:
                self.changed("The graphics card failed during this run, so the processor took over for a while (much slower). "
                             "The graphics card is tried again from time to time.")
            return self._session(model, "cpu").run(outs, feed)

    def _rest(self, worked):
        """The speed setting. It is a share of THIS computer's time: after every model call the models rest
        in proportion to how long that call just took here (at 'half', as long as it worked; at 'light',
        three times as long). Calls are a fraction of a second, so whatever else uses the graphics card gets
        its turn many times a second. A pause simply waits."""
        pace = self.pace
        if pace < 1.0:
            time.sleep(min(worked * (1.0 - pace) / max(pace, 0.05), 10.0))
        while self.paused and not (self.stop is not None and self.stop.is_set()):
            time.sleep(0.2)

    def separate(self, spec):
        s = self._session(SEPARATOR, self.where[SEPARATOR])
        return self._run(SEPARATOR, None, {s.get_inputs()[0].name: spec})[0]

    def emissions(self, wav, normalise):
        """Letter log-probabilities for every 20 ms. normalise: bring each window to a standard level first."""
        ctx, win = CONTEXT_S * SR, WINDOW_S * SR
        ext = int(np.ceil(len(wav) / win) * win - len(wav))
        padded = np.pad(wav, (ctx, ctx + ext))
        n = (len(padded) - 2 * ctx) // win
        cf = CONTEXT_S * 50
        outs = []
        for i in range(n):
            x = padded[i * win: i * win + win + 2 * ctx]
            if normalise:
                x = (x - x.mean()) / max(float(x.std()), 1e-5)
            lg = self._run(ALIGNER, ["logits"], {"input_values": x.astype(np.float32)[None, :]})[0]
            outs.append(lg[0][cf: -cf + 1])
        em = np.concatenate(outs, axis=0)
        ef = int(ext / SR * 50)
        if ef > 0:
            em = em[:-ef]
        em = em - em.max(axis=1, keepdims=True)
        em = em - np.log(np.exp(em).sum(axis=1, keepdims=True))
        return em.astype(np.float32), len(wav) / SR / em.shape[0]

    def hear(self, mix, voc):
        """mix, voc: mono 16 kHz (full song, isolated voice) -> what the timing step needs."""
        dur = len(mix) / SR
        voc = voc[: len(mix)] if len(voc) >= len(mix) else np.pad(voc, (0, len(mix) - len(voc)))
        em, spf = self.emissions(mix, False)
        emq, _ = self.emissions(lift_quiet(voc), True)
        n5 = len(voc) // 80
        env = np.sqrt((voc[: n5 * 80].reshape(n5, 80).astype(np.float64) ** 2).mean(axis=1))
        # half precision is what the timing rules were tuned and checked with
        return {"em": em.astype(np.float16), "emq": emq.astype(np.float16), "env": env.astype(np.float32), "dur": dur, "spf": spf}
