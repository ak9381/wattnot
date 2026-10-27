"""A small load generator for the H100 flex check.

It sends short questions to an OpenAI-compatible server (vLLM, for example) at a steady rate
and appends one JSON line per second to a progress file:

    {"t": 1792000000.0, "p99_ms": 2310.5, "pass_rate": 0.97, "rps": 9.8, "n": 98, "errors": 0}

`wattnot flex run --backend gpu --progress <file>` reads the last line each tick.

* Steady rate (open loop): requests are scheduled at `--rps` whether or not the server keeps
  up, and latency counts from the scheduled time. When a power cap slows the GPU, the p99 goes
  up the way a user would see it, instead of the generator quietly sending less.
* Quality: each prompt lists acceptable answers (`expect`). A reply passes if it contains one
  of them, ignoring case. Errors and timeouts count as failures.
* The numbers cover the last `--window` seconds.
* `--fake` runs without a server on a made-up latency model. It labels itself SIMULATED and is
  for dry runs of the plumbing only.

Standard library only. It never prints or stores an API key; the key, if any, is read from the
environment variable named by `--api-key-env`.
"""

from __future__ import annotations

import json
import math
import queue
import random
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PROMPTS = Path(__file__).resolve().parents[3] / "data" / "flex" / "load-prompts.jsonl"

Sender = Callable[[str, int], str]  # (prompt, max_tokens) -> reply text


@dataclass
class Prompt:
    id: str
    prompt: str
    expect: list[str]


def load_prompts(path: str | Path) -> list[Prompt]:
    out = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        d = json.loads(line)
        if not d.get("prompt") or not d.get("expect"):
            raise ValueError(f"{path}:{n}: each line needs 'prompt' and 'expect'")
        out.append(Prompt(str(d.get("id", f"p{n}")), d["prompt"], [str(x) for x in d["expect"]]))
    if not out:
        raise ValueError(f"{path}: no prompts")
    return out


def passes(reply: str, expect: list[str]) -> bool:
    low = reply.lower()
    return any(e.lower() in low for e in expect)


def p99(values: list[float]) -> float:
    """Nearest-rank 99th percentile."""

    if not values:
        raise ValueError("no values")
    s = sorted(values)
    return s[max(0, math.ceil(0.99 * len(s)) - 1)]


class Window:
    """Completed requests in the last `seconds`."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self._d: deque[tuple[float, float, bool]] = deque()  # (end, latency_ms, ok)
        self._lock = threading.Lock()
        self.total = 0
        self.errors = 0

    def add(self, end: float, latency_ms: float, ok: bool, *, error: bool = False) -> None:
        with self._lock:
            self._d.append((end, latency_ms, ok))
            self.total += 1
            self.errors += int(error)

    def snapshot(self, now: float) -> dict | None:
        with self._lock:
            while self._d and self._d[0][0] < now - self.seconds:
                self._d.popleft()
            rows = list(self._d)
            errors = self.errors
        if not rows:
            return None
        span = min(self.seconds, max(1.0, now - rows[0][0]))
        return {"t": round(now, 3), "p99_ms": round(p99([r[1] for r in rows]), 1),
                "pass_rate": round(sum(r[2] for r in rows) / len(rows), 4),
                "rps": round(len(rows) / span, 2), "n": len(rows), "errors": errors}


def http_sender(url: str, model: str, *, timeout: float = 60.0,
                api_key: str | None = None) -> Sender:
    """POST /v1/chat/completions on an OpenAI-compatible server."""

    endpoint = url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/v1/chat/completions" if not endpoint.endswith("/v1") \
            else "/chat/completions"

    def send(prompt: str, max_tokens: int) -> str:
        body = json.dumps({"model": model, "temperature": 0, "max_tokens": max_tokens,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (user-given URL)
            doc = json.loads(r.read())
        return doc["choices"][0]["message"]["content"] or ""

    return send


def fake_sender(expects: dict[str, list[str]], *, seed: int = 7, base_s: float = 0.15,
                accuracy: float = 0.97, sleep: Callable[[float], None] = time.sleep) -> Sender:
    """A stand-in server: made-up latency, mostly right answers. SIMULATED."""

    rng = random.Random(seed)
    lock = threading.Lock()

    def send(prompt: str, max_tokens: int) -> str:
        with lock:
            lat = base_s * (0.7 + 0.6 * rng.random())
            right = rng.random() < accuracy
        sleep(lat)
        ans = expects.get(prompt, ["?"])[0]
        return f"The answer is {ans}." if right else "I am not sure."

    return send


@dataclass
class LoadSummary:
    label: str
    seconds: float
    requests: int
    errors: int
    pass_rate: float | None
    p99_ms: float | None
    progress_lines: int
    progress_file: str

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def run_load(send: Sender, prompts: list[Prompt], progress: str | Path, *, rps: float = 8.0,
             concurrency: int = 32, duration_s: float = 0.0, window_s: float = 10.0,
             interval_s: float = 1.0, max_tokens: int = 64, seed: int = 7,
             label: str = "MEASURED", append: bool = False,
             stop: threading.Event | None = None,
             clock: Callable[[], float] = time.time) -> LoadSummary:
    """Run until `duration_s` passes (0 = until `stop` is set). Returns a summary."""

    if rps <= 0 or concurrency < 1:
        raise ValueError("rps must be above 0 and concurrency at least 1")
    stop = stop or threading.Event()
    path = Path(progress)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not append:
        path.write_text("", encoding="utf-8")
    win = Window(window_s)
    allp = Window(1e12)  # whole-run totals
    rng = random.Random(seed)
    jobs: queue.Queue[tuple[float, Prompt] | None] = queue.Queue(maxsize=max(64, int(rps * 30)))

    def worker() -> None:
        while True:
            job = jobs.get()
            if job is None:
                return
            due, p = job
            ok, err = False, False
            try:
                ok = passes(send(p.prompt, max_tokens), p.expect)
            except (OSError, ValueError, KeyError, urllib.error.URLError, TimeoutError):
                err = True
            end = clock()
            for w in (win, allp):
                w.add(end, (end - due) * 1000.0, ok, error=err)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(concurrency)]
    for t in threads:
        t.start()

    lines = 0
    t0 = clock()
    next_due = t0
    next_write = t0 + interval_s
    try:
        while not stop.is_set():
            now = clock()
            if duration_s and now - t0 >= duration_s:
                break
            while next_due <= now and not stop.is_set():
                try:
                    jobs.put_nowait((next_due, rng.choice(prompts)))
                except queue.Full:  # the server is far behind; count it, do not pile up
                    allp.add(clock(), 0.0, False, error=True)
                next_due += 1.0 / rps
            if now >= next_write:
                snap = win.snapshot(now)
                if snap:
                    with path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(snap) + "\n")
                    lines += 1
                next_write += interval_s
            stop.wait(min(0.05, max(0.0, min(next_due, next_write) - clock())))
    finally:
        for _ in threads:
            jobs.put(None)
        for t in threads:
            t.join(timeout=2.0)
    end = clock()
    snap = allp.snapshot(end)
    return LoadSummary(label, round(end - t0, 1), allp.total, allp.errors,
                       snap["pass_rate"] if snap else None, snap["p99_ms"] if snap else None,
                       lines, str(path))
