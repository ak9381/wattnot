"""Marginal-joule knee: the last watt worth capping (CPU-only, over already-measured points).

    python -m wattnot.longrun.knee --sweep runs/g2/sweep.json          # read a measured sweep
    python -m wattnot.longrun.knee --demo                              # the RUN2 sweep, built in

Block 2 / g2 lower the power cap step by step and stop "when J per correct answer flattens".
That flatten rule is a human eyeball. This turns it into a number: from a cap sweep that is
*already measured*, it derives, for each step down in watts,

    saved_per_w  = (J/correct at the higher cap - J/correct at the lower cap) / watts dropped
    p99_per_w    = (p99 delta%% at the lower cap - p99 delta%% at the higher cap) / watts dropped

and then names three things:

* ``knee_cap``   the cap where ``saved_per_w`` first falls below ``knee_frac`` of the best step
                 seen so far - the point of diminishing joule returns (informational).
* ``p99_wall``   the first step whose ``p99_per_w`` crosses ``p99_ceiling`` - where latency,
                 not energy, decides. The sweep should stop here even if the gate has not failed.
* ``recommended`` the lowest-watt point that still passes the gate *and* sits at or above the
                 p99 wall. That is the cap to keep.

Nothing here touches a GPU, the network or any money: it is a deterministic transform of the
numbers a sweep already wrote. The label is carried through from the sweep points (MEASURED when
they are measured), because the advice is only ever as good as its inputs - this module adds no
new measurement, only arithmetic over the ones it is given.

Prior art: Pareto-frontier and knee-of-the-curve picks are old. What is ours is applying the
marginal joule-per-correct-answer *and* the marginal p99 cost, per watt, to pick where a power-cap
sweep should stop - and writing the per-step table into the receipt so the stop is auditable.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

__all__ = ["Point", "marginal_steps", "analyze", "main"]

# Nominal board draw for an uncapped point (H100 SXM TDP). Only used to order a point that
# carries no explicit cap; never treated as a measured watt.
UNCAPPED_W = 700.0


def _num(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 6)


class Point:
    """One sweep point. ``cap_w`` None means uncapped (ordered at :data:`UNCAPPED_W`)."""

    __slots__ = ("cap_w", "j_per_good", "p99_delta_pct", "gate_pass", "label",
                 "good_per_hour", "throughput_label")

    def __init__(self, cap_w: float | None, j_per_good: float | None,
                 p99_delta_pct: float | None = 0.0, gate_pass: bool = True,
                 label: str = "MEASURED", good_per_hour: float | None = None,
                 throughput_label: str | None = None) -> None:
        self.cap_w = None if cap_w is None else float(cap_w)
        self.j_per_good = _num(j_per_good)
        self.p99_delta_pct = _num(p99_delta_pct)
        self.gate_pass = bool(gate_pass)
        self.label = label
        # Throughput: correct answers served per wall-clock hour at this cap. Only used by the cost
        # SLO (jslo); None when the sweep did not record it. Never synthesised here.
        self.good_per_hour = _num(good_per_hour)
        # Honesty of the throughput *on its own*, carried separately from ``label`` (which is the
        # joule number's honesty): a point can have MEASURED joules but a not-yet-measured
        # throughput, so any cost derived from it must degrade to ESTIMATE. None means "inherit
        # ``label``" - a point that is measured end to end.
        self.throughput_label = throughput_label

    @property
    def order_w(self) -> float:
        return UNCAPPED_W if self.cap_w is None else self.cap_w

    @classmethod
    def from_mapping(cls, m: Mapping) -> "Point":
        return cls(
            cap_w=m.get("cap_w"),
            j_per_good=(m.get("j_per_good_answer")
                        if m.get("j_per_good_answer") is not None
                        else m.get("j_per_good")),
            p99_delta_pct=m.get("p99_delta_pct", 0.0),
            gate_pass=m.get("gate_pass", True),
            label=m.get("label", "MEASURED"),
            good_per_hour=m.get("good_per_hour"),
            throughput_label=m.get("throughput_label"),
        )

    def as_dict(self) -> dict[str, Any]:
        d = {"cap_w": self.cap_w, "j_per_good_answer": _r(self.j_per_good),
             "p99_delta_pct": _r(self.p99_delta_pct), "gate_pass": self.gate_pass,
             "label": self.label}
        if self.good_per_hour is not None:
            d["good_per_hour"] = _r(self.good_per_hour)
            d["throughput_label"] = self.throughput_label or self.label
        return d


def _coerce(points: Iterable[Any]) -> list[Point]:
    out: list[Point] = []
    for p in points:
        out.append(p if isinstance(p, Point) else Point.from_mapping(p))
    # Highest watts first == least aggressive first; de-dup on ordering watt, keep the first seen.
    out.sort(key=lambda p: p.order_w, reverse=True)
    seen: set[float] = set()
    uniq: list[Point] = []
    for p in out:
        if p.order_w in seen:
            continue
        seen.add(p.order_w)
        uniq.append(p)
    return uniq


def marginal_steps(points: Iterable[Any]) -> list[dict[str, Any]]:
    """Per-step marginals, each from the higher cap (``from_w``) to the next lower (``to_w``)."""

    pts = _coerce(points)
    steps: list[dict[str, Any]] = []
    for hi, lo in zip(pts, pts[1:]):
        dw = hi.order_w - lo.order_w
        saved_per_w = p99_per_w = None
        if dw > 0 and hi.j_per_good is not None and lo.j_per_good is not None:
            saved_per_w = (hi.j_per_good - lo.j_per_good) / dw
        if dw > 0 and hi.p99_delta_pct is not None and lo.p99_delta_pct is not None:
            p99_per_w = (lo.p99_delta_pct - hi.p99_delta_pct) / dw
        steps.append({
            "from_w": hi.cap_w, "to_w": lo.cap_w, "watts_dropped": _r(dw),
            "saved_j_per_correct": _r(None if saved_per_w is None
                                      else hi.j_per_good - lo.j_per_good),
            "saved_per_w": _r(saved_per_w),
            "p99_per_w": _r(p99_per_w),
            "gate_pass": lo.gate_pass,
            "label": lo.label,
        })
    return steps


def analyze(points: Iterable[Any], *, knee_frac: float = 0.5,
            p99_ceiling: float = 0.1) -> dict[str, Any]:
    """Pick the cap to keep from a measured sweep. See the module docstring.

    ``knee_frac``   fraction of the best per-watt joule saving below which a step counts as
                    diminishing returns (default 0.5 = half the best step).
    ``p99_ceiling`` the marginal p99 cost (percent of p99 added per watt dropped) at or above
                    which latency, not energy, should stop the sweep (default 0.1 %%/W).
    """

    pts = _coerce(points)
    steps = marginal_steps(pts)
    label = pts[0].label if pts else "MEASURED"

    if len(pts) < 2:
        return {"recommended": None, "knee_cap": None, "p99_wall": None,
                "reason": "need at least two sweep points", "steps": steps,
                "points": [p.as_dict() for p in pts], "label": label,
                "params": {"knee_frac": knee_frac, "p99_ceiling": p99_ceiling}}

    # Knee: first step whose joule saving per watt drops below knee_frac of the best seen so far.
    best_per_w = 0.0
    knee_cap: float | None = None
    for s in steps:
        spw = s["saved_per_w"]
        if spw is None:
            continue
        if best_per_w > 0 and spw < knee_frac * best_per_w and knee_cap is None:
            knee_cap = s["from_w"]
        best_per_w = max(best_per_w, spw)

    # p99 wall: first step whose marginal p99 cost crosses the ceiling. The wall is the higher
    # cap of that step (the last cap reached before latency took over).
    p99_wall: float | None = None
    for s in steps:
        ppw = s["p99_per_w"]
        if ppw is not None and ppw >= p99_ceiling:
            p99_wall = s["from_w"]
            break

    # Recommend the lowest-watt point that passes the gate and is not below the p99 wall.
    recommended: float | None = None
    for p in reversed(pts):  # most aggressive first
        if not p.gate_pass:
            continue
        if p99_wall is not None and p.cap_w is not None and p.cap_w < p99_wall:
            continue
        recommended = p.cap_w
        break

    if recommended is None:
        reason = "no cap passed the gate at or above the p99 wall"
    elif recommended == pts[-1].cap_w:
        reason = "most aggressive measured cap still passes the gate and the p99 wall"
    else:
        reason = (f"stopped at {recommended:g} W: lower caps fail the gate or "
                  f"cross the p99 wall")

    return {"recommended": recommended, "knee_cap": knee_cap, "p99_wall": p99_wall,
            "reason": reason, "steps": steps, "points": [p.as_dict() for p in pts],
            "label": label, "params": {"knee_frac": knee_frac, "p99_ceiling": p99_ceiling}}


# The RUN2 H100 sweep from the README table: J per correct answer and p99 delta% are MEASURED.
# good_per_hour is a *synthetic* throughput (correct answers per wall-clock hour) - it illustrates
# the trade-off (a lower cap saves joules per answer but serves fewer answers per hour, so it costs
# *more* money per answer at a fixed $/h, the tension the cost SLO in jslo surfaces) but it has not
# been measured, so it is labelled ESTIMATE and any cost derived from it is an ESTIMATE too. Real
# throughput numbers come from the H100 run.
RUN2_SWEEP: list[dict[str, Any]] = [
    {"cap_w": None, "j_per_good_answer": 17.03, "p99_delta_pct": 0.0, "gate_pass": True, "good_per_hour": 1600.0, "throughput_label": "ESTIMATE"},
    {"cap_w": 500, "j_per_good_answer": 16.38, "p99_delta_pct": 0.7, "gate_pass": True, "good_per_hour": 1550.0, "throughput_label": "ESTIMATE"},
    {"cap_w": 450, "j_per_good_answer": 14.69, "p99_delta_pct": 1.8, "gate_pass": True, "good_per_hour": 1450.0, "throughput_label": "ESTIMATE"},
    {"cap_w": 400, "j_per_good_answer": 13.81, "p99_delta_pct": 4.7, "gate_pass": True, "good_per_hour": 1380.0, "throughput_label": "ESTIMATE"},
    {"cap_w": 350, "j_per_good_answer": 12.42, "p99_delta_pct": 7.7, "gate_pass": True, "good_per_hour": 1240.0, "throughput_label": "ESTIMATE"},
    {"cap_w": 325, "j_per_good_answer": 11.84, "p99_delta_pct": 10.9, "gate_pass": False, "good_per_hour": 1180.0, "throughput_label": "ESTIMATE"},
]


def _load_sweep(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    if isinstance(data, Mapping):
        data = data.get("points") or data.get("sweep") or []
    return list(data)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Marginal-joule knee: where a power-cap sweep stops.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--sweep", type=Path, help="JSON: a list of points or {'points': [...]}")
    src.add_argument("--demo", action="store_true", help="use the built-in RUN2 sweep")
    ap.add_argument("--knee-frac", type=float, default=0.5)
    ap.add_argument("--p99-ceiling", type=float, default=0.1)
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = ap.parse_args(argv)

    points = RUN2_SWEEP if args.demo else _load_sweep(args.sweep)
    out = analyze(points, knee_frac=args.knee_frac, p99_ceiling=args.p99_ceiling)

    if args.json:
        print(json.dumps(out, indent=2))
        return 0
    rec = out["recommended"]
    print(f"[{out['label']}] recommended cap: "
          f"{'none' if rec is None else f'{rec:g} W'}  -  {out['reason']}")
    if out["knee_cap"] is not None:
        print(f"  knee (joules flatten) at: {out['knee_cap']:g} W")
    if out["p99_wall"] is not None:
        print(f"  p99 wall at:              {out['p99_wall']:g} W")
    for s in out["steps"]:
        frm = "uncapped" if s["from_w"] is None else f"{s['from_w']:g}W"
        gate = "pass" if s["gate_pass"] else "FAIL"
        print(f"  {frm:>8} -> {s['to_w']:g}W: "
              f"saved {s['saved_per_w']} J/correct per W, "
              f"p99 +{s['p99_per_w']} %/W [{gate}]")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
