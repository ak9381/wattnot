"""Power-cap controller.

Two modes:

* `step_down` (offline, gated): lower the cap one step at a time, run a
  trial at each step and pass it through the gates. The first failing step
  is reverted to the last cap that passed, and the search stops there.
* `CapController.tick` (live): every period, read p99 and nudge the cap.
  Lower it while p99 has headroom, raise it when p99 nears the SLA. A
  dead band and a minimum hold time stop it from flapping.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from wattnot.control.nvml_ctl import CapGuard


@dataclass(frozen=True)
class StepOutcome:
    cap_w: int
    passed: bool
    failed_gates: tuple[str, ...] = ()


def _emit(emit, kind: str, data: dict, step: str) -> None:
    if emit is not None:
        emit.emit(kind, data, step_id=step)


def step_down(guard: CapGuard, *, start_w: int, floor_w: int, step_w: int,
              trial: Callable[[int], object], gate: Callable[[object], object],
              emit=None) -> tuple[int, list[StepOutcome]]:
    """Return the lowest cap that passed every gate, and every step tried.

    `trial(cap_w)` runs the workload at that cap. `gate(result)` returns an
    object with `.passed` and `.failed` (a `Decision`). The GPU is left at
    the returned cap.
    """

    if step_w <= 0:
        raise ValueError("step_w must be positive")
    best = guard.set_cap_w(start_w)
    outcomes: list[StepOutcome] = []
    cap = best
    while cap - step_w >= max(floor_w, guard.min_w):
        cap = guard.set_cap_w(cap - step_w)
        decision = gate(trial(cap))
        step = f"cap:{cap}"
        if decision.passed:
            outcomes.append(StepOutcome(cap, True))
            _emit(emit, "action.applied", {"change": {"power_cap_w": cap}}, step)
            best = cap
            continue
        failed = tuple(decision.failed)
        outcomes.append(StepOutcome(cap, False, failed))
        guard.set_cap_w(best)
        _emit(emit, "action.rolled_back",
              {"change": {"power_cap_w": cap}, "restored_cap_w": best,
               "failed_gates": list(failed)}, step)
        break
    return best, outcomes


class CapController:
    """Live p99-following cap controller for one GPU."""

    def __init__(self, guard: CapGuard, *, p99_sla_s: float, min_w: int, max_w: int,
                 step_w: int = 25, period_s: float = 0.1, headroom: float = 0.8,
                 danger: float = 0.95, hold_s: float = 0.5,
                 clock: Callable[[], float] = time.monotonic, emit=None) -> None:
        if not 0 < headroom < danger <= 1:
            raise ValueError("need 0 < headroom < danger <= 1")
        self.guard = guard
        self.sla = p99_sla_s
        self.min_w = guard.clamp(min_w)
        self.max_w = guard.clamp(max_w)
        self.step_w = step_w
        self.period_s = period_s
        self.headroom = headroom
        self.danger = danger
        self.hold_s = hold_s
        self.clock = clock
        self.emit = emit
        self.changes: list[tuple[float, int]] = []
        self._last_change = -float("inf")

    def set_cap(self, watts: int) -> int:
        watts = max(self.min_w, min(self.max_w, watts))
        applied = self.guard.set_cap_w(watts)
        now = self.clock()
        self.changes.append((now, applied))
        self._last_change = now
        _emit(self.emit, "action.applied", {"change": {"power_cap_w": applied}},
              f"cap:{applied}")
        return applied

    def tick(self, p99_s: float) -> int:
        """One control step. Returns the cap after the step."""

        cap = self.guard.cap_w()
        now = self.clock()
        ratio = p99_s / self.sla
        if ratio >= self.danger and cap < self.max_w:
            return self.set_cap(cap + 2 * self.step_w)  # back off fast, no hold
        if now - self._last_change < self.hold_s:
            return cap
        if ratio < self.headroom and cap > self.min_w:
            return self.set_cap(cap - self.step_w)
        return cap

    def restore(self) -> None:
        self.guard.restore()

    def loop(self, read_p99: Callable[[], float], stop: threading.Event,
             sleep: Callable[[float], None] = time.sleep) -> None:
        try:
            while not stop.is_set():
                self.tick(read_p99())
                sleep(self.period_s)
        finally:
            self.restore()
