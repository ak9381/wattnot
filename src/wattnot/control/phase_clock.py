"""Phase-aware GPU clock sidecar for vLLM (RUN3 add-on; related prior art: DynamoLLM).

Every --interval-s it reads vLLM's Prometheus counters (prompt and generation tokens), classifies
the window as prefill-heavy or decode-heavy, and locks the GPU clocks through NVML: high for
prefill (compute-bound), lower for decode (memory-bound). It works per scheduler window of
batch steps (default 100 ms), not per request. Clocks are reset on exit.

    python -m wattnot.control.phase_clock --url http://127.0.0.1:8000 --high 1980 --low 1410 \
        --log phase.jsonl [--mock]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import time
import urllib.request

PROMPT = re.compile(r"^vllm:prompt_tokens_total(?:\{[^}]*\})?\s+([0-9.eE+]+)", re.M)
GEN = re.compile(r"^vllm:generation_tokens_total(?:\{[^}]*\})?\s+([0-9.eE+]+)", re.M)


def counters(text: str) -> tuple[float, float]:
    p = sum(float(x) for x in PROMPT.findall(text))
    g = sum(float(x) for x in GEN.findall(text))
    return p, g


def classify(dp: float, dg: float, ratio: float = 4.0) -> str:
    """prefill if prompt tokens in the window outweigh generated ones by `ratio`; idle if none."""
    if dp <= 0 and dg <= 0:
        return "idle"
    return "prefill" if dp >= ratio * max(dg, 1.0) else "decode"


class MockNVML:
    def __init__(self):
        self.calls = []

    def lock(self, lo, hi):
        self.calls.append(("lock", lo, hi))

    def reset(self):
        self.calls.append(("reset",))


class NVML:
    def __init__(self, index: int = 0):
        import pynvml
        pynvml.nvmlInit()
        self.n = pynvml
        self.h = pynvml.nvmlDeviceGetHandleByIndex(index)

    def lock(self, lo, hi):
        self.n.nvmlDeviceSetGpuLockedClocks(self.h, lo, hi)

    def reset(self):
        self.n.nvmlDeviceResetGpuLockedClocks(self.h)


class Controller:
    def __init__(self, gpu, high: int, low: int, ratio: float = 4.0, log=None):
        self.gpu, self.high, self.low, self.ratio, self.log = gpu, high, low, ratio, log
        self.state = None
        self.prev = None
        self.switches = 0
        self.windows = {"prefill": 0, "decode": 0, "idle": 0}

    def step(self, text: str, now: float) -> str:
        p, g = counters(text)
        if self.prev is None:
            self.prev = (p, g)
            return "idle"
        dp, dg = p - self.prev[0], g - self.prev[1]
        self.prev = (p, g)
        ph = classify(dp, dg, self.ratio)
        self.windows[ph] += 1
        want = self.high if ph == "prefill" else self.low if ph == "decode" else None
        if want is not None and want != self.state:
            self.gpu.lock(want, want)
            self.state = want
            self.switches += 1
            if self.log:
                self.log.write(json.dumps({"t": round(now, 3), "phase": ph, "clock_mhz": want,
                                           "d_prompt": dp, "d_gen": dg}) + "\n")
        return ph

    def close(self):
        self.gpu.reset()
        self.state = None


def metrics_base(url: str) -> str:
    """vLLM serves /metrics at the server root, not under the OpenAI /v1 prefix (run 1103:
    the driver passed .../v1, every scrape was a 404 and every window counted 0)."""
    u = url.rstrip("/")
    return u[:-3] if u.endswith("/v1") else u


class MixLoad:
    """Two workers alternating a long-prompt/short-answer request (prefill-heavy) with a
    short-prompt/long-answer one (decode-heavy) against the served model, until stopped."""

    LONG = ("Summarise the rack telemetry below in one word. " + "GPU0 power 512 W temp 61 C; " * 180)
    SHORT = "Write a short story about a data center at night."

    def __init__(self, base: str, duration_s: float, stop: dict, workers: int = 2):
        import threading
        self.base, self.stop, self.t_end = base, stop, time.time() + duration_s
        self.n = {"prefill_req": 0, "decode_req": 0, "errors": 0}
        self.model = None
        self.threads = [threading.Thread(target=self._run, args=(i,), daemon=True)
                        for i in range(workers)]

    def _model(self):
        if self.model is None:
            d = json.loads(urllib.request.urlopen(self.base + "/v1/models", timeout=5).read())  # noqa: S310
            self.model = d["data"][0]["id"]
        return self.model

    def _req(self, prompt: str, max_tokens: int) -> None:
        body = json.dumps({"model": self._model(), "prompt": prompt, "max_tokens": max_tokens,
                           "temperature": 0}).encode()
        rq = urllib.request.Request(self.base + "/v1/completions", data=body,
                                    headers={"Content-Type": "application/json"})
        urllib.request.urlopen(rq, timeout=60).read()  # noqa: S310

    def _run(self, i: int) -> None:
        k = i
        while not self.stop["v"] and time.time() < self.t_end:
            try:
                if k % 2 == 0:
                    self._req(self.LONG, 4)
                    self.n["prefill_req"] += 1
                else:
                    self._req(self.SHORT, 192)
                    self.n["decode_req"] += 1
            except Exception:  # noqa: BLE001
                self.n["errors"] += 1
                time.sleep(1)
            k += 1

    def start(self):
        for t in self.threads:
            t.start()

    def stats(self) -> dict:
        return dict(self.n, workers=len(self.threads), label="MEASURED")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--high", type=int, default=1980)
    ap.add_argument("--low", type=int, default=1410)
    ap.add_argument("--ratio", type=float, default=4.0)
    ap.add_argument("--interval-s", type=float, default=0.1)
    ap.add_argument("--duration-s", type=float, default=0, help="0 = until SIGTERM")
    ap.add_argument("--log", default="phase.jsonl")
    ap.add_argument("--summary", default=None)
    ap.add_argument("--mock", action="store_true", help="mock NVML (dry run)")
    a = ap.parse_args(argv)
    base = metrics_base(a.url)
    gpu = MockNVML() if a.mock else NVML()
    stop = {"v": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(v=True))
    t0 = time.time()
    c_err = {"n": 0}
    for path in (a.log, a.summary):
        if path and os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
    load = None
    if not a.mock:
        try:   # a light prefill/decode mix so the windows have traffic to classify
            load = MixLoad(base, a.duration_s or 180, stop)
            load.start()
        except Exception:  # noqa: BLE001
            load = None
    with open(a.log, "a") as lg:
        c = Controller(gpu, a.high, a.low, a.ratio, lg)
        try:
            while not stop["v"] and (not a.duration_s or time.time() - t0 < a.duration_s):
                try:
                    txt = urllib.request.urlopen(base + "/metrics", timeout=2).read().decode()  # noqa: S310
                    c.step(txt, time.time() - t0)
                except OSError:
                    c_err["n"] += 1
                time.sleep(a.interval_s)
        finally:
            stop["v"] = True
            c.close()
    s = {"switches": c.switches, "windows": c.windows, "high_mhz": a.high, "low_mhz": a.low,
         "interval_s": a.interval_s, "granularity": "per scheduler window of batch steps, not per request",
         "prior_art": "DynamoLLM (phase-aware frequency scaling for LLM inference)", "mock": a.mock,
         "metrics_url": base + "/metrics", "metrics_errors": c_err["n"],
         "load": load.stats() if load else None}
    if a.summary:
        open(a.summary, "w").write(json.dumps(s, indent=1))
    print(json.dumps(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
