"""Measure a llama-server try: decode tok/s, GPU joules (nvidia-smi), host joules (RAPL).

What each number is called:

- tok/s: MEASURED. It comes from llama-server's own `timings` block (decoded tokens
  over decode milliseconds) and falls back to our wall clock.
- GPU joules: MEASURED from `nvidia-smi` power.draw sampled twice a second and
  integrated. No nvidia-smi (a CPU dry run) means no GPU number at all, not zero.
- Host joules (CPU package + DRAM): MEASURED through RAPL when the VM shows it.
  Otherwise ESTIMATE from an assumed watts-per-thread times CPU busy share, and an
  estimate is never counted as a saving (see gate.energy_verdict).

Nothing here starts a GPU or spends money. `LocalRig` only talks to a llama-server
that this process starts on the same machine.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from wattnot.runner.workload import WorkloadItem
from wattnot.spark_open.hostpower import Rapl
from wattnot.spark_open.knobs import SparkKnobs
from wattnot.spark_open.llama_server import LlamaServer

_LOOPBACK = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# ESTIMATE constants for the no-RAPL fallback. Shown in every record that uses them.
CPU_W_PER_THREAD = 3.5  # about a server core's share of package power under load
CPU_IDLE_SHARE = 0.30  # fraction of that drawn when idle
DRAM_W_PER_GB = 0.4  # DDR5 DIMM power per resident GB
ESTIMATE_NOTE = (
    f"{CPU_W_PER_THREAD} W/thread x (idle {CPU_IDLE_SHARE:.0%} + busy share), "
    f"plus {DRAM_W_PER_GB} W per resident GB. Assumed, not measured."
)


# ---- GPU power ----------------------------------------------------------------------------


def smi_watts(binary: str = "nvidia-smi") -> float | None:
    """Total board power across visible GPUs in watts, or None."""

    try:
        out = subprocess.run(
            [binary, "--query-gpu=power.draw", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout
        vals = [float(x) for x in out.split() if x.replace(".", "", 1).isdigit()]
        return sum(vals) if vals else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


class GpuPower:
    """Sample GPU watts in a thread and integrate to joules (trapezoid)."""

    def __init__(
        self,
        read: Callable[[], float | None] | None = None,
        *,
        interval_s: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._read = read or (smi_watts if shutil.which("nvidia-smi") else (lambda: None))
        self.interval_s, self._clock = interval_s, clock
        self._pts: list[tuple[float, float]] = []
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    @property
    def available(self) -> bool:
        return self._read() is not None

    def _loop(self) -> None:
        while not self._stop.is_set():
            w = self._read()
            if w is not None:
                self._pts.append((self._clock(), w))
            self._stop.wait(self.interval_s)

    def start(self) -> None:
        self._pts.clear()
        self._stop.clear()
        w = self._read()
        if w is not None:
            self._pts.append((self._clock(), w))
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def stop(self) -> dict:
        self._stop.set()
        if self._t:
            self._t.join(timeout=5)
        w = self._read()
        if w is not None:
            self._pts.append((self._clock(), w))
        return integrate(self._pts)


def integrate(points: Sequence[tuple[float, float]]) -> dict:
    """Trapezoid over (seconds, watts). None when there are fewer than two points."""

    if len(points) < 2:
        return {
            "joules": None,
            "label": "unavailable (no GPU power reading)",
            "avg_w": None,
            "samples": len(points),
        }
    j = sum(
        (t1 - t0) * (w0 + w1) / 2 for (t0, w0), (t1, w1) in zip(points, points[1:], strict=False)
    )
    span = points[-1][0] - points[0][0]
    return {
        "joules": round(j, 3),
        "label": "MEASURED",
        "avg_w": round(j / span, 1) if span else None,
        "samples": len(points),
    }


# ---- host power ---------------------------------------------------------------------------


def cpu_busy_share(path: str = "/proc/stat") -> tuple[float, float]:
    """(busy jiffies, total jiffies) from the aggregate cpu line."""

    f = Path(path).read_text().splitlines()[0].split()[1:]
    v = [float(x) for x in f]
    idle = v[3] + (v[4] if len(v) > 4 else 0.0)
    total = sum(v[:8])
    return total - idle, total


def estimate_host_joules(
    seconds: float, busy_share: float, threads: int, resident_gb: float
) -> float:
    cpu_w = CPU_W_PER_THREAD * threads * (CPU_IDLE_SHARE + (1 - CPU_IDLE_SHARE) * busy_share)
    return seconds * (cpu_w + DRAM_W_PER_GB * resident_gb)


class HostPower:
    """RAPL when present (MEASURED), otherwise a labelled ESTIMATE."""

    def __init__(
        self,
        rapl: Rapl | None = None,
        *,
        stat: Callable[[], tuple[float, float]] = cpu_busy_share,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rapl = rapl if rapl is not None else Rapl()
        self._stat, self._clock = stat, clock
        self._h0 = self._s0 = self._t0 = None

    @property
    def measured(self) -> bool:
        return self.rapl.available

    def start(self) -> None:
        self._t0 = self._clock()
        self._h0 = self.rapl.read() if self.measured else None
        try:
            self._s0 = self._stat()
        except (OSError, ValueError, IndexError):
            self._s0 = None

    def stop(self, *, threads: int, resident_gb: float) -> dict:
        now = self._clock()
        secs = now - (now if self._t0 is None else self._t0)
        if self._h0 is not None:
            return {
                "joules": round(self.rapl.joules_between(self._h0, self.rapl.read()), 3),
                "label": "MEASURED",
                "method": "RAPL powercap (CPU package + DRAM)",
            }
        share = 0.5
        try:
            b1, t1 = self._stat()
            if self._s0 and t1 > self._s0[1]:
                share = max(0.0, min(1.0, (b1 - self._s0[0]) / (t1 - self._s0[1])))
        except (OSError, ValueError, IndexError):
            pass
        return {
            "joules": round(estimate_host_joules(secs, share, threads, resident_gb), 3),
            "label": "ESTIMATE",
            "method": ESTIMATE_NOTE,
            "busy_share": round(share, 3),
        }


# ---- one chat call to a local llama-server ------------------------------------------------


@dataclass(frozen=True)
class Call:
    item_id: str
    text: str
    latency_s: float
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str | None
    cached_tokens: int = 0
    decode_ms: float | None = None  # llama-server timings.predicted_ms
    status: int = 200

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"


class LocalChat:
    """OpenAI-style /chat/completions against llama-server on loopback. Temperature 0."""

    def __init__(
        self,
        base_url: str,
        model: str = "spark-open",
        *,
        timeout_s: float = 900,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.timeout_s, self._clock = model, timeout_s, clock

    def complete(
        self, item: WorkloadItem, *, max_tokens: int | None = None, extra: dict | None = None
    ) -> Call:
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "user", "content": item.prompt}],
            "max_tokens": max_tokens or item.max_tokens,
            **(extra or {}),
        }
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )
        t0 = self._clock()
        with _LOOPBACK.open(req, timeout=self.timeout_s) as r:
            data = json.loads(r.read())
        lat = self._clock() - t0
        u = data.get("usage") or {}
        ch = (data.get("choices") or [{}])[0]
        tm = data.get("timings") or {}
        return Call(
            item.id,
            (ch.get("message") or {}).get("content") or "",
            lat,
            int(u.get("prompt_tokens", 0)),
            int(u.get("completion_tokens", 0)),
            ch.get("finish_reason"),
            int((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)),
            float(tm["predicted_ms"]) if "predicted_ms" in tm else None,
        )


def decode_tok_s(calls: Sequence[Call]) -> float | None:
    """MEASURED decode speed: decoded tokens over decode time (wall clock if none given)."""

    toks = sum(c.completion_tokens for c in calls)
    ms = [c.decode_ms for c in calls]
    if toks and all(m is not None for m in ms) and sum(ms) > 0:
        return round(toks / (sum(ms) / 1000), 3)
    wall = sum(c.latency_s for c in calls)
    return round(toks / wall, 3) if toks and wall > 0 else None


# ---- the rig the tuner calls ---------------------------------------------------------------


@dataclass
class LocalRig:
    """Start llama-server with a try's knobs, replay prompts, read power. Not simulated."""

    models_dir: Path
    items: Sequence[WorkloadItem]
    out: Path
    server: LlamaServer | None = None
    gpu: GpuPower = field(default_factory=GpuPower)
    host: HostPower = field(default_factory=HostPower)
    warmup: bool = True
    resident_gb: float = 0.0  # RAM the model holds, for the DRAM estimate
    simulated: bool = False
    dry_run_note: str = ""

    def __post_init__(self) -> None:
        self.server = self.server or LlamaServer(self.models_dir, log_dir=self.out / "logs")

    def run(self, knobs: SparkKnobs) -> dict:
        load_s = self.server.start(knobs)
        chat = LocalChat(self.server.base_url)
        try:
            if self.warmup and self.items:
                chat.complete(self.items[0], max_tokens=8)
            self.gpu.start()
            self.host.start()
            t0 = time.monotonic()
            calls = [chat.complete(it) for it in self.items]
            wall = time.monotonic() - t0
            g = self.gpu.stop()
            h = self.host.stop(threads=knobs.threads, resident_gb=self.resident_gb)
        finally:
            self.server.stop()
        return {
            "outputs": {c.item_id: c.text for c in calls},
            "latencies": [c.latency_s for c in calls],
            "truncated": {c.item_id: c.truncated for c in calls},
            "gpu_j": g["joules"],
            "gpu_label": g["label"],
            "gpu_avg_w": g["avg_w"],
            "host_j": h["joules"],
            "host_label": h["label"],
            "host_method": h["method"],
            "out_tokens": sum(c.completion_tokens for c in calls),
            "tok_s": decode_tok_s(calls),
            "wall_s": round(wall, 3),
            "load_s": load_s,
            "calls": calls,
        }
