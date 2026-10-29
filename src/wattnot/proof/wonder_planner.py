"""Muse Wonder as the cap-sweep planner (long run, block 2).

Wraps `wattnot.meta.wonder.WonderSupervisor` (the client the live Wonder pass uses) in the
`propose(history) -> {kind, cap_w, ...}` shape the orchestrator's sweep loop expects, adding the
clock profile. The bounded candidate list is 300-375 W in 25 W steps, each with driver clocks
("base") and, while the card accepts clock locks, locked SM clocks.

Wonder only proposes. The gates still decide acceptance, and the local Pareto check stops the
sweep when J-per-correct-answer stops improving, whatever the model says.

When a step reports that the clock lock was refused (`clock_fallback`), `caps_only()` removes every
locked candidate, so the rest of the sweep is caps-only and no trial claims a clock it did not get.

Cost axis: the supervisor's front uses `usd_per_1k`. We feed it J per correct answer relative to
stock (1.0 = stock) because on a rented GPU the hour costs the same whatever the cap; energy per
correct answer is what the cap changes. The field name is kept for the supervisor's schema; the
summary says what it holds.
"""

from __future__ import annotations

from collections.abc import Sequence

from wattnot.meta.wonder import WonderSupervisor

LO_W, HI_W, STEP_W = 300, 375, 25
CLOCKS = ("base", "locked-high", "locked-mid")


def candidates(lo: int = LO_W, hi: int = HI_W, clocks: Sequence[str] = CLOCKS
               ) -> list[tuple[int, str]]:
    return [(w, c) for w in range(hi, lo - 1, -STEP_W) for c in clocks]


def to_trial(h: dict) -> dict:
    q = float(h.get("quality_pass_pct") or 0.0)
    rel_j = 1.0 + float(h.get("energy_change_pct") or 0.0) / 100.0
    stock_q = float(h.get("stock_pass_pct") or q or 1.0)
    rel_jpc = rel_j * (stock_q / q) if q else 99.0
    return {"cap_w": h["cap_w"], "clock_profile": h.get("clock_profile", "base"),
            "joules_per_correct": round(rel_jpc, 5),
            # gate-failing trials are not on the front: quality 0
            "quality": round(q / 100.0, 5) if h.get("accepted") else 0.0,
            "p99_delta": round(float(h.get("p99_change_pct") or 0.0) / 100.0, 5),
            "usd_per_1k": round(rel_jpc, 5),
            "label": h.get("label", "MEASURED")}


class WonderPlanner:
    name = "muse-wonder"
    partner = "meta"

    def __init__(self, supervisor: WonderSupervisor | None = None, *, lo: int = LO_W,
                 hi: int = HI_W, clocks: Sequence[str] = CLOCKS, mock: bool = True) -> None:
        self.sup = supervisor or WonderSupervisor(mock=mock, candidates=candidates(lo, hi, clocks),
                                                  threshold=0.01, patience=3)
        if supervisor is not None:
            self.sup.candidates = candidates(lo, hi, clocks)
        self.model = self.sup.model
        self.mock = self.sup.mock
        self.last_usage: dict = {}
        self.context: dict = {}
        self.clock_fallback: str | None = None
        self.trace_ids = self.sup.trace_ids

    def caps_only(self, reason: str) -> None:
        if self.clock_fallback is None:
            self.clock_fallback = reason
        self.sup.candidates = [c for c in self.sup.candidates if c[1] == "base"]

    def propose(self, history: Sequence[dict]) -> dict:
        for h in history:
            if h.get("clock_fallback"):
                self.caps_only(h["clock_fallback"])
        trials = [to_trial(h) for h in history]
        try:
            d = self.sup.next_trial(trials)
        except Exception as e:  # noqa: BLE001 - Wonder down: stop proposing, the grid fills in
            return {"kind": "stop", "cap_w": None, "by": "wonder-error",
                    "reason": f"Wonder call failed ({type(e).__name__}); grid fills the rest",
                    "muse_error": str(e)[:160]}
        self.last_usage = dict(d.tokens)
        by = self.name if not self.mock else "muse-wonder-mock"
        base = {"by": by, "reason": d.reason, "trace_id": d.trace_id, "model": self.model,
                "usage": dict(d.tokens), "explanation": d.reason[:280],
                "stop_flattened": d.stop_flattened}
        if d.action == "stop":
            return {**base, "kind": "stop", "cap_w": None, "clock_profile": None}
        return {**base, "kind": "try", "cap_w": int(d.cap_w), "clock_profile": d.clock_profile}
