"""The shed controller: cut GPU draw to a target without breaking the latency budget.

One `tick` per second. It works on smoothed readings and follows these rules:

1. First move: set the cap just where it starts to bind (the draw measured before the event).
   Caps above that do nothing, so stepping down from the card's maximum would waste time.
2. While the draw is above target and p99 has headroom, lower the cap one step, then wait
   `hold_s` before the next move.
3. If p99 gets near the SLA, or answer quality drops, raise the cap two steps and remember
   that cap as a floor. It never goes below the floor again in this event.
4. If the next step would go below the card's minimum cap or the floor, stop and say why
   (`cap_floor` or `p99`). The run reports a partial shed rather than pushing on.
5. After the event, raise the cap in steps until the draw and p99 are back to normal.

Same idea as the live `CapController`, with a target and a floor added.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class FlexConfig:
    dt_s: float = 1.0
    pre_s: int = 30  # window before the event, used to measure the baseline
    post_s: int = 90  # window after the event, used to measure ramp-back
    step_w: int = 10
    hold_s: float = 2.0
    ema_alpha: float = 0.5
    sla_slack: float = 0.5  # SLA = baseline p99 * (1 + slack)
    danger: float = 0.98  # raise the cap when smoothed p99 passes this share of the SLA
    headroom: float = 0.92  # only lower the cap while smoothed p99 is below this share
    quality_drop_pts: float = 1.0  # tolerated drop in pass rate, in percentage points
    breach_ticks: int = 2  # ticks in a row over the line before the cap is raised
    tol: float = 0.02  # target counts as reached within this share
    settle_ticks: int = 3  # ticks in a row at or under target before it counts as reached

    def validate(self) -> None:
        if self.dt_s <= 0 or self.step_w <= 0 or self.settle_ticks < 1:
            raise ValueError("dt_s, step_w and settle_ticks must be positive")
        if not 0 < self.ema_alpha <= 1:
            raise ValueError("ema_alpha must be in (0, 1]")
        if not 0 < self.headroom < self.danger <= 1:
            raise ValueError("need 0 < headroom < danger <= 1")
        if self.sla_slack <= 0:
            raise ValueError("sla_slack must be positive")

    def to_dict(self) -> dict:
        return asdict(self)


class FlexController:
    def __init__(self, backend, cfg: FlexConfig, *, base_power_w: float, base_p99_ms: float,
                 base_pass_rate: float, target_pct: float) -> None:
        cfg.validate()
        self.b, self.cfg = backend, cfg
        self.base_power_w, self.base_p99_ms = base_power_w, base_p99_ms
        self.base_pass = base_pass_rate
        self.sla_ms = base_p99_ms * (1 + cfg.sla_slack)
        self.target_w = base_power_w * (1 - target_pct / 100)
        self.floor_w = backend.min_w
        self.cap_w = backend.cap_w()
        self.power_ema: float | None = None
        self.p99_ema: float | None = None
        self.last_change_t = -1e9
        self.under = 0
        self.breach = 0
        self.reached_t: float | None = None
        self.limit: str | None = None
        self.moves: list[tuple[float, int]] = []
        self._started = False

    def _ema(self, prev, x):
        a = self.cfg.ema_alpha
        return x if prev is None else a * x + (1 - a) * prev

    def _set(self, t: float, w: int) -> None:
        w = self.b.set_cap_w(int(w))
        if w != self.cap_w:
            self.moves.append((t, w))
            self.last_change_t = t
        self.cap_w = w

    def _observe(self, r) -> None:
        self.power_ema = self._ema(self.power_ema, r.power_w)
        self.p99_ema = self._ema(self.p99_ema, r.p99_ms)

    def tick(self, t: float, r) -> None:
        """One step during the event. `t` is seconds since the run started."""

        c = self.cfg
        self._observe(r)
        if not self._started:
            self._started = True
            first = math.ceil(self.base_power_w / c.step_w) * c.step_w
            if first < self.cap_w:
                self._set(t, first)
            return
        low_quality = (r.pass_rate * 100) < (self.base_pass * 100 - c.quality_drop_pts)
        over = low_quality or self.p99_ema > self.sla_ms * c.danger
        self.breach = self.breach + 1 if over else 0
        if over and self.breach >= c.breach_ticks:
            self.breach = 0
            self._set(t, self.cap_w + 2 * c.step_w)
            self.floor_w = max(self.floor_w, self.cap_w)
            self.limit = "quality" if low_quality else "p99"
            self.under = 0
            self.reached_t = None
            return
        if over:
            return
        if self.power_ema <= self.target_w * (1 + c.tol):
            self.under += 1
            if self.under >= c.settle_ticks and self.reached_t is None:
                self.reached_t = t
            return
        self.under = 0
        self.reached_t = None
        nxt = self.cap_w - c.step_w
        if nxt < self.floor_w:
            self.limit = self.limit or ("cap_floor" if self.floor_w <= self.b.min_w else "p99")
            if self.floor_w <= self.b.min_w:
                self.limit = "cap_floor"
            return
        if t - self.last_change_t < c.hold_s:
            return
        if self.p99_ema > self.sla_ms * c.headroom:
            self.limit = "p99"
            return
        self._set(t, nxt)

    def tick_restore(self, t: float, r, restore_w: int) -> None:
        """One step after the event: raise the cap toward its old value."""

        self._observe(r)
        if self.cap_w < restore_w:
            self._set(t, min(restore_w, self.cap_w + 2 * self.cfg.step_w))
