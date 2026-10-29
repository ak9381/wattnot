"""Joule-SLO: energy *and* cost budgets as first-class request parameters (CPU-only, over measured
points).

    python -m wattnot.longrun.jslo --demo --budget 14.0                 # under 14 J/correct?
    python -m wattnot.longrun.jslo --demo --budget 16.0 --cost-budget 1.7 --usd-per-hour 2.5
    python -m wattnot.longrun.jslo --sweep runs/g2/sweep.json --budget 13.0 --json

Today a caller can ask an inference API for a *cost* ceiling or a *latency* ceiling. No one can
ask for an *energy* ceiling - "serve this class of request under J joules per correct answer, and
prove you did." This module is that admission control. Given a budget in joules-per-correct, an
optional budget in dollars-per-1k-correct, and a p99 ceiling, over a sweep of serving configs that
is *already measured* (same ``Point`` shape as the knee advisor), it answers one question with a
proof-ready verdict:

* ``admit``       there is a measured config that passes the quality gate, holds p99, and lands at
                  or under *both* budgets. It returns the *least aggressive* such config - the one
                  that honours the SLOs while leaving the most quality/latency headroom (don't push
                  harder than the SLO asks) - plus the ``greenest`` config for callers who want to
                  bank the extra joules, the headroom under each budget, and which budget ``binds``.
* ``infeasible``  no measured config meets the budgets under the gate and p99. It returns the
                  ``floor`` for each budget - the lowest joules-per-correct and lowest
                  dollars-per-1k that *are* achievable - so the caller learns the tightest promise
                  this workload can make, and ``binds`` names which constraint said no.

The cost budget is where this earns its keep: a lower power cap saves joules per answer but serves
fewer answers per hour, so at a fixed $/h it can cost *more* money per answer. The joule-optimal
config is often not the dollar-optimal one, and ``binds`` makes that trade visible.

Honesty of each number is carried, not assumed:
* joules-per-correct is MEASURED (or ESTIMATE) exactly as the sweep point is labelled;
* dollars-per-1k-correct is ``$/h`` (a published *list price*, not a metered bill) times the
  config's throughput. It is tagged ``MEASURED-derived`` - measured time at a list price, never a
  bare MEASURED - *only* when both the joule number and the throughput are measured. If either is an
  estimate the cost is an ``ESTIMATE`` too; when the throughput is the un-measured part the verdict
  carries ``cost_note`` saying so. The built-in RUN2 throughput is synthetic, so its costs are
  ESTIMATEs with that note - real throughput comes from the H100 run.

Why this is a lever and not just a lookup: it turns the sweep WattNot already runs into a contract.
The verdict, the chosen config, the achieved joules/cost and the floors are all arithmetic over
measured points, so the whole decision drops straight into a signed receipt - an energy-and-cost
SLA anyone can re-check.

Nothing here touches a GPU, the network or any money (the $/h is a constant the caller passes in).
It adds no new measurement, only the admission rule over the numbers a sweep already wrote.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from wattnot.longrun.knee import RUN2_SWEEP, Point

__all__ = ["admit", "main"]

# Shown whenever a cost is priced off a throughput that has not been measured yet. The throughput
# in the built-in RUN2 sweep is synthetic, so its costs are ESTIMATEs carrying this note.
THROUGHPUT_NOTE = "throughput not yet measured; real numbers come from the H100 run"


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 6)


def _coerce(points: Iterable[Any]) -> list[Point]:
    out: list[Point] = []
    for p in points:
        out.append(p if isinstance(p, Point) else Point.from_mapping(p))
    # Least aggressive first (highest watts), so "the first feasible" is the safest config.
    out.sort(key=lambda p: p.order_w, reverse=True)
    seen: set[float] = set()
    uniq: list[Point] = []
    for p in out:
        if p.order_w in seen:
            continue
        seen.add(p.order_w)
        uniq.append(p)
    return uniq


def _serveable(p: Point, p99_ceiling_pct: float) -> bool:
    """A config can back an SLO only if it passes the gate, holds p99 and has a measured joule cost."""
    return (p.gate_pass
            and p.j_per_good is not None
            and p.p99_delta_pct is not None
            and p.p99_delta_pct <= p99_ceiling_pct)


def _cost_per_1k(p: Point,
                 usd_per_hour: float | None) -> tuple[float | None, str | None, str | None]:
    """Dollars per 1000 correct answers for one config, with an honest basis label and a note.

    Cost = ``$/h`` (a published *list price* the caller passes in, not a metered bill) times the
    config's wall-clock time, i.e. ``usd_per_hour / good_per_hour * 1000``. It is ``MEASURED-derived``
    (measured time at a list price, never a bare MEASURED) *only* when both the point and its
    throughput are MEASURED; if either the joule number (``label``) or the throughput
    (``throughput_label``, defaulting to ``label``) is an ESTIMATE, the cost is an ESTIMATE too -
    you cannot price a measured time you never measured. When the throughput is the un-measured
    part, :data:`THROUGHPUT_NOTE` is returned so the caller can say why. Returns ``(None, None,
    None)`` when the $/h or the throughput is missing - we never invent a throughput.
    """
    if usd_per_hour is None or p.good_per_hour is None or p.good_per_hour <= 0:
        return None, None, None
    cost = usd_per_hour / p.good_per_hour * 1000.0
    tput_label = p.throughput_label or p.label
    if p.label == "MEASURED" and tput_label == "MEASURED":
        return cost, "MEASURED-derived", None
    note = THROUGHPUT_NOTE if tput_label != "MEASURED" else None
    return cost, "ESTIMATE", note


def admit(points: Iterable[Any], *, budget_j_per_correct: float,
          p99_ceiling_pct: float = 10.0,
          budget_usd_per_1k_correct: float | None = None,
          usd_per_hour: float | None = None) -> dict[str, Any]:
    """Decide whether a request's budgets can be served, and with which config.

    ``budget_j_per_correct``        the energy SLO: the most joules per correct answer allowed.
    ``p99_ceiling_pct``             the latency SLO: the most p99 may rise (percent) vs baseline.
    ``budget_usd_per_1k_correct``   optional cost SLO: the most dollars per 1000 correct answers.
    ``usd_per_hour``                the GPU's list price per hour, needed to price any config; the
                                    cost SLO is only enforced when both this and a per-point
                                    measured throughput are present.
    """

    pts = _coerce(points)
    label = pts[0].label if pts else "MEASURED"
    base = {"budget_j_per_correct": budget_j_per_correct,
            "p99_ceiling_pct": p99_ceiling_pct,
            "budget_usd_per_1k_correct": budget_usd_per_1k_correct,
            "usd_per_hour": usd_per_hour, "label": label}
    # Cost fields default to None so consumers (receipt, dashboard) always see stable keys.
    no_cost = {"achieved_usd_per_1k_correct": None, "cost_basis": None, "cost_note": None,
               "cost_floor_usd_per_1k_correct": None, "cost_floor_basis": None}

    serveable = [p for p in pts if _serveable(p, p99_ceiling_pct)]
    if not serveable:
        return {**base, "verdict": "infeasible", "binds": "quality/p99", "chosen": None,
                "greenest": None, "achieved_j_per_correct": None, "headroom_j": None,
                "floor_j_per_correct": None, "headroom_usd": None, **no_cost,
                "reason": "no measured config passes the quality gate within the p99 ceiling"}

    # The physical floors: the greenest config, and the cheapest config that still serves.
    greenest = min(serveable, key=lambda p: p.j_per_good)  # type: ignore[arg-type]
    floor_j = greenest.j_per_good
    costed = [(p, *_cost_per_1k(p, usd_per_hour)) for p in serveable]
    priced = [(p, c, b, n) for (p, c, b, n) in costed if c is not None]
    cost_floor = cost_floor_basis = cost_floor_note = None
    if priced:
        pc, cost_floor, cost_floor_basis, cost_floor_note = min(priced, key=lambda t: t[1])  # type: ignore[arg-type]

    def _j_ok(p: Point) -> bool:
        return p.j_per_good <= budget_j_per_correct  # type: ignore[operator]

    def _c_ok(p: Point) -> bool:
        if budget_usd_per_1k_correct is None:
            return True
        cost, _, _ = _cost_per_1k(p, usd_per_hour)
        return cost is not None and cost <= budget_usd_per_1k_correct

    within_j = [p for p in serveable if _j_ok(p)]
    within_c = [p for p in serveable if _c_ok(p)]
    # serveable[] is least-aggressive first, so the admissible list keeps that order.
    admissible = [p for p in serveable if _j_ok(p) and _c_ok(p)]

    cost_fields = {"cost_floor_usd_per_1k_correct": _r(cost_floor),
                   "cost_floor_basis": cost_floor_basis}

    if not admissible:
        # Name the budget that said no. If both sets are non-empty but disjoint, each config that
        # is cheap enough is too energy-hungry (or vice versa): the two budgets bind together.
        if not within_j:
            binds, why = "joule", (f"budget {budget_j_per_correct:g} J/correct is below the floor "
                                   f"{floor_j:g} J/correct")
        elif budget_usd_per_1k_correct is not None and not within_c:
            if cost_floor is None:
                binds, why = "cost", ("the cost budget cannot be checked: no $/h or no measured "
                                      "throughput for any serveable config")
            else:
                binds, why = "cost", (f"budget ${budget_usd_per_1k_correct:g}/1k is below the cost "
                                      f"floor ${cost_floor:g}/1k")
        else:
            binds, why = "both", ("no single config meets both budgets: the cheaper configs exceed "
                                  "the joule budget and the greener ones exceed the cost budget")
        return {**base, "verdict": "infeasible", "binds": binds, "chosen": None,
                "greenest": greenest.cap_w, "achieved_j_per_correct": None, "headroom_j": None,
                "floor_j_per_correct": _r(floor_j), "achieved_usd_per_1k_correct": None,
                "cost_basis": None, "cost_note": cost_floor_note, "headroom_usd": None,
                **cost_fields, "reason": f"{why}; relax the budget or the p99 ceiling"}

    # Honour the SLOs with the least-aggressive config that meets them.
    chosen = admissible[0]
    headroom_j = budget_j_per_correct - chosen.j_per_good  # type: ignore[operator]
    chosen_cost, chosen_basis, chosen_note = _cost_per_1k(chosen, usd_per_hour)
    headroom_usd = (None if (budget_usd_per_1k_correct is None or chosen_cost is None)
                    else budget_usd_per_1k_correct - chosen_cost)

    # Which budget binds: the one the chosen config sits closest to (smallest headroom as a
    # fraction of its budget). With no cost budget, the joule budget is the only one that can bind.
    if budget_usd_per_1k_correct is None or chosen_cost is None or headroom_usd is None:
        binds = "joule"
    else:
        jfrac = headroom_j / budget_j_per_correct if budget_j_per_correct else 0.0
        cfrac = headroom_usd / budget_usd_per_1k_correct if budget_usd_per_1k_correct else 0.0
        binds = "both" if abs(jfrac - cfrac) < 1e-9 else ("joule" if jfrac < cfrac else "cost")

    cap_txt = "uncapped" if chosen.cap_w is None else f"{chosen.cap_w:g} W"
    reason = (f"served at {cap_txt}: {chosen.j_per_good:g} J/correct, "
              f"{headroom_j:g} J/correct under budget")
    if chosen_cost is not None:
        reason += f"; ${chosen_cost:.4g}/1k correct ({chosen_basis})"
    return {**base, "verdict": "admit", "binds": binds, "chosen": chosen.cap_w,
            "greenest": greenest.cap_w, "achieved_j_per_correct": _r(chosen.j_per_good),
            "headroom_j": _r(headroom_j), "floor_j_per_correct": _r(floor_j),
            "achieved_usd_per_1k_correct": _r(chosen_cost), "cost_basis": chosen_basis,
            "cost_note": chosen_note, "headroom_usd": _r(headroom_usd), **cost_fields,
            "reason": reason}


def _load_sweep(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    if isinstance(data, Mapping):
        data = data.get("points") or data.get("sweep") or []
    return list(data)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Joule-SLO: admit a request under an energy budget.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--sweep", type=Path, help="JSON: a list of points or {'points': [...]}")
    src.add_argument("--demo", action="store_true", help="use the built-in RUN2 sweep")
    ap.add_argument("--budget", type=float, required=True, help="joules per correct answer ceiling")
    ap.add_argument("--p99-ceiling", type=float, default=10.0, help="percent p99 may rise")
    ap.add_argument("--cost-budget", type=float, default=None,
                    help="optional dollars per 1000 correct answers ceiling")
    ap.add_argument("--usd-per-hour", type=float, default=None,
                    help="GPU list price per hour, needed to price the cost budget")
    ap.add_argument("--json", action="store_true", help="print the full verdict as JSON")
    args = ap.parse_args(argv)

    points = RUN2_SWEEP if args.demo else _load_sweep(args.sweep)
    out = admit(points, budget_j_per_correct=args.budget, p99_ceiling_pct=args.p99_ceiling,
                budget_usd_per_1k_correct=args.cost_budget, usd_per_hour=args.usd_per_hour)

    if args.json:
        print(json.dumps(out, indent=2))
        return 0
    chosen = out["chosen"]
    where = "none" if chosen is None else f"{chosen:g} W"
    cost_txt = "" if args.cost_budget is None else f", ${args.cost_budget:g}/1k correct"
    print(f"[{out['label']}] {out['verdict'].upper()} (budget {args.budget:g} J/correct{cost_txt}): "
          f"{out['reason']}")
    print(f"  binds: {out['binds']}")
    if out.get("cost_note"):
        print(f"  note: {out['cost_note']}")
    if out["verdict"] == "admit":
        print(f"  config to serve: {where}   greenest available: "
              f"{'uncapped' if out['greenest'] is None else f'{out['greenest']:g} W'}   "
              f"floor: {out['floor_j_per_correct']} J/correct")
        if out["cost_floor_usd_per_1k_correct"] is not None:
            print(f"  cheapest available: ${out['cost_floor_usd_per_1k_correct']}/1k correct "
                  f"({out['cost_floor_basis']})")
    elif out["floor_j_per_correct"] is not None:
        print(f"  tightest promissable budget: {out['floor_j_per_correct']} J/correct"
              + ("" if out["cost_floor_usd_per_1k_correct"] is None
                 else f", ${out['cost_floor_usd_per_1k_correct']}/1k correct"))
    return 0 if out["verdict"] == "admit" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
