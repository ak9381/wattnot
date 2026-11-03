"""An NVML-shaped H100 for when no real GPU is attached.

It replays the recorded H100 RUN2 data (data/h100-run2): the 1 Hz board-power trace of the
whole session, and the per-cap results of the sweep (mean watts under load, energy and p99
change, quality pass rate, accepted or rejected). Every number it hands out is labelled REPLAY
(read straight from the recording) or SIMULATED (in between recorded points, e.g. 375 W).

`open_gpu()` picks the backend: real NVML when a GPU answers, else this replay. So the same
node agent code reports MEASURED on a real card and REPLAY/SIMULATED here, with no flag flips.
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RUN2 = ROOT / "data" / "h100-run2"
STOCK_W = 700


def _num(s: str) -> float:
    return float(s.strip().split()[0])


def load_trace(path: Path = RUN2 / "gpu" / "gpu_trace.csv") -> list[dict]:
    rows = []
    with Path(path).open() as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            try:
                rows.append({"power_w": _num(row[1]), "temp_c": int(_num(row[3])), "util": int(_num(row[7]))})
            except (ValueError, IndexError):
                continue
    return rows


def load_steps(d: Path = RUN2 / "vm" / "steps") -> dict[int, dict]:
    out = {}
    for f in sorted(Path(d).glob("cap*.json")):
        s = json.loads(f.read_text())
        out[int(s["cap_w"])] = {
            "cap_w": int(s["cap_w"]), "accepted": bool(s["accepted"]), "why_not": list(s.get("why_not") or []),
            "stock_w": float(s["mean_w"]["stock"]), "cap_mean_w": float(s["mean_w"]["cap"]),
            "energy_change_pct": round(float(s["energy_change_pct"]["mean"]), 2),
            "p99_change_pct": round(float(s["p99_change_pct"]["mean"]), 2),
            "quality_stock_pct": s["quality"]["stock_pct"], "quality_cap_pct": s["quality"]["cap_pct"],
            "quality_checked": s["quality"]["checked"], "max_temp_c": s.get("max_temp_c"), "label": "REPLAY"}
    return out


def step_at(steps: dict[int, dict], cap: int, p99_limit: float = 10.0) -> dict:
    """The recorded step for this cap, or a SIMULATED one interpolated between recorded caps."""
    if cap in steps:
        return dict(steps[cap])
    ks = sorted(steps)
    lo = max((k for k in ks if k < cap), default=None)
    hi = min((k for k in ks if k > cap), default=None)
    if lo is None or hi is None:   # outside the recording: we do not invent it
        return {"cap_w": cap, "label": "NOT RECORDED", "accepted": False,
                "why_not": [f"{cap} W is outside the recorded caps {ks[0]}-{ks[-1]} W"]}
    a, b = steps[lo], steps[hi]
    w = (cap - lo) / (hi - lo)
    mix = lambda k: round(a[k] + (b[k] - a[k]) * w, 2)  # noqa: E731
    p99 = mix("p99_change_pct")
    ok = p99 <= p99_limit and a["quality_cap_pct"] >= a["quality_stock_pct"] - 2
    return {"cap_w": cap, "accepted": ok, "why_not": [] if ok else [f"p99 up {p99}% (limit {p99_limit}%)"],
            "stock_w": mix("stock_w"), "cap_mean_w": mix("cap_mean_w"),
            "energy_change_pct": mix("energy_change_pct"), "p99_change_pct": p99,
            "quality_stock_pct": a["quality_stock_pct"], "quality_cap_pct": min(a["quality_cap_pct"], b["quality_cap_pct"]),
            "quality_checked": a["quality_checked"], "max_temp_c": max(a["max_temp_c"], b["max_temp_c"]),
            "label": "SIMULATED", "between": [lo, hi]}


class SimulatedH100:
    """The NVML calls the node agent uses, answered from the RUN2 recording."""

    label = "REPLAY"
    name = "NVIDIA H100 80GB HBM3 (simulated: RUN2 replay)"

    def __init__(self, trace: list[dict] | None = None, steps: dict | None = None, loaded: bool = True):
        tr = trace if trace is not None else load_trace()
        # replay the loaded part of the session (the sweep), not the idle warm-up
        self.trace = [r for r in tr if r["util"] > 0] if loaded else tr
        self.trace = self.trace or tr
        self.steps = steps if steps is not None else load_steps()
        self.limit_w = STOCK_W
        self.i = 0
        self.energy_mj = 0
        self.history: list[dict] = []

    # NVML-shaped surface (milliwatts / millijoules like pynvml)
    def nvmlDeviceGetCount(self) -> int:
        return 1

    def nvmlDeviceGetName(self, h=None) -> str:
        return self.name

    def nvmlDeviceGetPowerManagementLimitConstraints(self, h=None) -> tuple[int, int]:
        return (200_000, 700_000)

    def nvmlDeviceGetPowerManagementLimit(self, h=None) -> int:
        return int(self.limit_w * 1000)

    def nvmlDeviceSetPowerManagementLimit(self, h, mw: int) -> None:
        lo, hi = self.nvmlDeviceGetPowerManagementLimitConstraints()
        if not lo <= mw <= hi:
            raise ValueError(f"limit {mw} mW outside {lo}-{hi}")
        self.limit_w = mw / 1000
        self.history.append({"set_w": self.limit_w})

    def _scale(self) -> float:
        if self.limit_w >= STOCK_W:
            return 1.0
        st = step_at(self.steps, int(self.limit_w))
        return st["cap_mean_w"] / st["stock_w"] if "cap_mean_w" in st else self.limit_w / STOCK_W

    def nvmlDeviceGetPowerUsage(self, h=None) -> int:
        """One 1 s tick of the recorded trace, scaled to the current cap and clipped to it."""
        r = self.trace[self.i % len(self.trace)]
        self.i += 1
        w = min(r["power_w"] * self._scale(), self.limit_w)
        self.energy_mj += int(w * 1000)
        return int(w * 1000)

    def nvmlDeviceGetTotalEnergyConsumption(self, h=None) -> int:
        return self.energy_mj

    def nvmlDeviceGetTemperature(self, h=None, sensor=0) -> int:
        return int(self.trace[(self.i - 1) % len(self.trace)]["temp_c"])


class RealNvml:
    """Thin pynvml wrapper with the same surface (one handle per index)."""

    label = "MEASURED"

    def __init__(self, nvml, index: int = 0):
        self.n, self.h = nvml, nvml.nvmlDeviceGetHandleByIndex(index)
        self.steps = load_steps()   # recorded results are only used as a prior, never reported

    def __getattr__(self, k):
        f = getattr(self.n, k)
        return lambda *a: f(self.h, *a[1:]) if a else f(self.h)

    def nvmlDeviceGetCount(self) -> int:
        return self.n.nvmlDeviceGetCount()


def open_gpu(prefer: str | None = None, nvml_module=None):
    """MEASURED when real NVML answers with at least one GPU, else the RUN2 replay.

    prefer: "sim" forces the replay, "real" forces NVML (raises if missing), None/"auto" picks.
    The env var WATTNOT_GPU can set it too.
    """
    prefer = (prefer or os.environ.get("WATTNOT_GPU") or "auto").lower()
    if prefer != "sim":
        try:
            nv = nvml_module
            if nv is None:
                import pynvml as nv  # type: ignore
            nv.nvmlInit()
            if nv.nvmlDeviceGetCount() > 0:
                return RealNvml(nv)
        except Exception:  # noqa: BLE001  no driver, no lib, no GPU
            if prefer == "real":
                raise
    return SimulatedH100()
