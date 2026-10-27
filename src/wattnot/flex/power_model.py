"""A small software model of one GPU under a power cap. Every number it makes is SIMULATED.

The model has four ideas, all of them guesses until we measure an H100:

* Idle draw is fixed. Working draw grows with clock speed as `clock ** k` (power rises
  faster than speed, which is why capping saves energy per request).
* Throughput grows as `clock ** beta`. Decoding a big model is mostly waiting on memory,
  so beta is below 1: a slower clock costs less speed than power.
* The cap limits the draw. A cap above what the load needs does nothing. A cap below it
  forces the clock down until the draw fits, and never below `clock_min`.
* Latency is the base time at full clock, divided by the clock speed, times a queueing
  factor that grows as the GPU gets busy: p99 = base / speed * (1 + q * busy / (1 - busy)).

`PowerModel.observe(cap_w, load_rps)` returns what a meter and a load generator would see.
Nothing here is fitted to a real GPU. The H100 run replaces these guesses with readings;
until then the numbers are for the demo only and are never quoted as results.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

LABEL = "SIMULATED"
NOTE = ("software power model with guessed parameters; replaced by NVML readings "
        "on the H100 run")


@dataclass(frozen=True)
class Observation:
    power_w: float
    p99_ms: float
    clock: float  # fraction of full clock, 0..1
    busy: float  # fraction of time the GPU is working, 0..1 (1 means saturated)
    throughput_rps: float
    saturated: bool


@dataclass(frozen=True)
class PowerModel:
    idle_w: float = 70.0
    tdp_w: float = 700.0
    intensity: float = 0.65  # share of (tdp - idle) the workload reaches at full clock, full busy
    k: float = 2.5  # working power ~ clock ** k
    beta: float = 0.6  # speed ~ clock ** beta
    clock_min: float = 0.35
    capacity_rps: float = 20.0  # requests per second at full clock
    base_p99_ms: float = 900.0  # p99 at full clock when nearly idle
    queue_q: float = 1.0

    @property
    def dyn_w(self) -> float:
        return (self.tdp_w - self.idle_w) * self.intensity

    def speed(self, clock: float) -> float:
        return clock ** self.beta

    def _busy(self, clock: float, load_rps: float) -> float:
        return load_rps / (self.capacity_rps * self.speed(clock))

    def draw_w(self, clock: float, load_rps: float) -> float:
        busy = min(1.0, self._busy(clock, load_rps))
        return self.idle_w + self.dyn_w * clock ** self.k * busy

    def clock_for_cap(self, cap_w: float, load_rps: float) -> float:
        """Highest clock whose draw fits under the cap (bisection; draw rises with clock)."""

        if self.draw_w(1.0, load_rps) <= cap_w:
            return 1.0
        if self.draw_w(self.clock_min, load_rps) > cap_w:
            return self.clock_min
        lo, hi = self.clock_min, 1.0
        for _ in range(48):
            mid = (lo + hi) / 2
            if self.draw_w(mid, load_rps) <= cap_w:
                lo = mid
            else:
                hi = mid
        return lo

    def observe(self, cap_w: float, load_rps: float) -> Observation:
        c = self.clock_for_cap(cap_w, load_rps)
        raw_busy = self._busy(c, load_rps)
        busy = min(raw_busy, 0.995)
        speed = self.speed(c)
        p99 = self.base_p99_ms / speed * (1 + self.queue_q * busy / (1 - busy))
        sat = raw_busy >= 1.0
        served = min(load_rps, self.capacity_rps * speed)
        return Observation(self.draw_w(c, load_rps), p99, c, min(raw_busy, 1.0), served, sat)

    def to_dict(self) -> dict:
        return {"label": LABEL, "note": NOTE, **asdict(self)}
