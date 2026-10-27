"""Run one grid event through the controller and summarise what happened.

`run_flex(backend, event, cfg)` walks three phases at one tick per `dt_s`:
before (measure the baseline), event (shed), after (ramp back). The backend is anything
with `min_w`, `max_w`, `cap_w()`, `set_cap_w(w)` and `observe(t)`. `SimBackend` is the
software model; `HardwareBackend` reads a real GPU and is meant for the H100 run.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field

from wattnot.flex.controller import FlexConfig, FlexController
from wattnot.flex.power_model import PowerModel
from wattnot.flex.signal import ShedEvent


@dataclass(frozen=True)
class Reading:
    power_w: float
    p99_ms: float
    pass_rate: float  # 0..1
    throughput_rps: float


class SimBackend:
    """The software power model with steady load and seeded noise. SIMULATED."""

    label = "SIMULATED"

    def __init__(self, model: PowerModel | None = None, *, load_rps: float = 10.0,
                 pass_rate: float = 0.82, noise: float = 0.015, seed: int = 7,
                 min_w: int = 200, max_w: int = 700,
                 quality_at: Callable[[int], float] | None = None) -> None:
        self.model = model or PowerModel()
        self.load_rps, self.pass_rate, self.noise = load_rps, pass_rate, noise
        self.min_w, self.max_w = min_w, max_w
        self._cap = max_w
        self._rng = random.Random(seed)
        self._quality_at = quality_at

    def cap_w(self) -> int:
        return self._cap

    def set_cap_w(self, w: int) -> int:
        self._cap = max(self.min_w, min(self.max_w, int(w)))
        return self._cap

    def observe(self, t: float) -> Reading:
        n = lambda: 1 + self._rng.gauss(0, self.noise)  # noqa: E731
        load = self.load_rps * (1 + self._rng.gauss(0, self.noise))
        o = self.model.observe(self._cap, load)
        q = self._quality_at(self._cap) if self._quality_at else self.pass_rate
        return Reading(o.power_w * n(), o.p99_ms * n(), q, o.throughput_rps)

    def meter(self) -> dict:
        return {"source": "software power model v1", "label": "SIMULATED",
                "model": self.model.to_dict(), "load_rps": self.load_rps}


class HardwareBackend:
    """A real GPU through NVML, for the H100 run. The caller supplies p99 and pass rate.

    `read_p99_ms()` and `read_pass_rate()` come from the load generator (for example the
    last line of its progress file). Power comes from NVML. The cap is held by `CapGuard`,
    which puts the old cap back on exit.
    """

    label = "MEASURED"

    def __init__(self, nvml, handle, guard, *, read_p99_ms, read_pass_rate, read_rps=lambda: 0.0):
        self.nvml, self.handle, self.guard = nvml, handle, guard
        self.min_w, self.max_w = guard.min_w, guard.max_w
        self._p99, self._pass, self._rps = read_p99_ms, read_pass_rate, read_rps

    def cap_w(self) -> int:
        return self.guard.cap_w()

    def set_cap_w(self, w: int) -> int:
        return self.guard.set_cap_w(w)

    def restore_cap(self, w: int) -> int:
        """Put a limit back (never needs a permit)."""
        return self.guard.set_cap_w(w, restoring=True)

    def observe(self, t: float) -> Reading:
        return Reading(self.nvml.power_usage_mw(self.handle) / 1000.0, float(self._p99()),
                       float(self._pass()), float(self._rps()))

    def meter(self) -> dict:
        return {"source": "NVML power draw, 1 Hz", "label": "MEASURED"}


@dataclass
class Tick:
    t_s: float
    phase: str  # before | event | after
    power_w: float
    p99_ms: float
    pass_rate: float
    throughput_rps: float
    cap_w: int
    target_w: float | None
    sla_ms: float | None


@dataclass
class FlexRun:
    event: ShedEvent
    cfg: FlexConfig
    ticks: list[Tick]
    moves: list[tuple[float, int]]
    baseline: dict
    result: dict
    meter: dict = field(default_factory=dict)


def _mean(xs):
    return statistics.fmean(xs) if xs else 0.0


def run_flex(backend, event: ShedEvent, cfg: FlexConfig | None = None, *,
             sleep: Callable[[float], None] | None = None) -> FlexRun:
    cfg = cfg or FlexConfig()
    cfg.validate()
    n_pre = int(cfg.pre_s / cfg.dt_s)
    n_event = int(event.duration_s / cfg.dt_s)
    n_post = int(cfg.post_s / cfg.dt_s)
    if n_pre < 5:
        raise ValueError("pre_s must give at least 5 ticks to measure a baseline")
    original_cap = backend.cap_w()
    ticks: list[Tick] = []
    ctl: FlexController | None = None
    try:
        for i in range(n_pre + n_event + n_post):
            t = i * cfg.dt_s
            if i < n_pre:
                phase = "before"
            elif i < n_pre + n_event:
                phase = "event"
            else:
                phase = "after"
            r = backend.observe(t)
            if i == n_pre:
                pre = [k for k in ticks if k.phase == "before"][n_pre // 3:]
                ctl = FlexController(
                    backend, cfg, base_power_w=_mean([k.power_w for k in pre]),
                    base_p99_ms=_mean([k.p99_ms for k in pre]),
                    base_pass_rate=_mean([k.pass_rate for k in pre]),
                    target_pct=event.target_pct)
            if phase == "event":
                ctl.tick(t, r)
            elif phase == "after":
                ctl.tick_restore(t, r, original_cap)
            ticks.append(Tick(t, phase, r.power_w, r.p99_ms, r.pass_rate, r.throughput_rps,
                              backend.cap_w(), ctl.target_w if ctl else None,
                              ctl.sla_ms if ctl else None))
            if sleep:
                sleep(cfg.dt_s)
    finally:
        getattr(backend, "restore_cap", backend.set_cap_w)(original_cap)  # never leave it capped
    meter = backend.meter() if hasattr(backend, "meter") else {}
    return FlexRun(event, cfg, ticks, ctl.moves, _baseline(ticks, n_pre),
                   _summarise(ticks, ctl, event, cfg, n_pre, n_event), meter)


def _baseline(ticks, n_pre) -> dict:
    pre = ticks[n_pre // 3:n_pre]
    return {"power_w": _mean([k.power_w for k in pre]), "p99_ms": _mean([k.p99_ms for k in pre]),
            "pass_rate": _mean([k.pass_rate for k in pre]),
            "throughput_rps": _mean([k.throughput_rps for k in pre]), "ticks": len(pre)}


def _phase(ticks, name):
    return [k for k in ticks if k.phase == name]


def _summarise(ticks, ctl, event, cfg, n_pre, n_event) -> dict:
    base_p = ctl.base_power_w
    ev = _phase(ticks, "event")
    after = _phase(ticks, "after")
    t_event = ev[0].t_s
    t_end = t_event + n_event * cfg.dt_s
    reached = ctl.reached_t
    # The settled window: from when the target was first held, else the last 30% of the event.
    settled_from = reached if reached is not None else ev[int(len(ev) * 0.7)].t_s
    settled = [k for k in ev if k.t_s >= settled_from]
    shed_w = base_p - _mean([k.power_w for k in settled])
    delivered_pct = max(0.0, 100 * shed_w / base_p)
    if reached is not None:
        status, limit = "full", None
    elif delivered_pct >= 1.0:
        status, limit = "partial", ctl.limit or "p99"
    else:
        status, limit = "none", ctl.limit or "p99"
    sla = ctl.sla_ms
    over = [k for k in ev if k.p99_ms > sla]
    # ramp-back: first tick after the event where draw and p99 are both near baseline
    base_p99 = ctl.base_p99_ms
    ramp = None
    for k in after:
        if abs(k.power_w - base_p) <= 0.05 * base_p and k.p99_ms <= base_p99 * 1.10:
            ramp = k.t_s - t_end + cfg.dt_s
            break
    return {
        "status": status, "limit": limit,
        "target_pct": event.target_pct, "target_w": round(ctl.target_w, 2),
        "delivered_pct": round(delivered_pct, 2), "delivered_w": round(shed_w, 2),
        "seconds_to_target": None if reached is None else round(reached - t_event, 1),
        "ramp_back_s": None if ramp is None else round(ramp, 1),
        "cap_changes": sum(1 for t, _ in ctl.moves if t < t_end),
        "settled_from_s": round(settled_from, 1),
        "settled_cap_w": settled[-1].cap_w if settled else None,
        "p99_ms": {
            "before_mean": round(base_p99, 1),
            "event_mean": round(_mean([k.p99_ms for k in settled]), 1),
            "event_max": round(max(k.p99_ms for k in ev), 1),
            "sla": round(sla, 1),
            "sla_violation_s": round(len(over) * cfg.dt_s, 1),
        },
        "pass_rate": {"before": round(ctl.base_pass, 4),
                      "event": round(_mean([k.pass_rate for k in settled]), 4)},
    }
