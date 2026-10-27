"""Trial loop with Muse Spark choosing each step.

Per step: build the telemetry summary, ask the decision maker, apply the
choice (cap through the guard, knob through a callback), run a trial, run
the gates. A failed gate reverts to the last passing config and is fed
back into the next summary, so the model sees what went wrong.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from wattnot import recommend
from wattnot.control.nvml_ctl import CapGuard
from wattnot.planner import Decision, DecisionMaker, summarize


@dataclass
class Measured:
    p99_s: float
    wh_per_req: float


@dataclass
class LoopResult:
    cap_w: int
    knobs: dict
    decisions: list[Decision] = field(default_factory=list)
    gates: list[dict] = field(default_factory=list)
    recommendations: object = None  # wattnot.recommend.Recommendations
    physical: dict | None = None  # the rack ceiling step, when a photo audit was given


def planned_loop(guard: CapGuard, maker: DecisionMaker, *, baseline: Measured,
                 trial: Callable[[int, dict], Measured],
                 gate: Callable[[Measured], object], knobs: dict, step_w: int = 25,
                 slack: float = 0.10, max_steps: int = 6, emit=None,
                 rec_opts: dict | None = None, summarizer=None,
                 physical: tuple | None = None) -> LoopResult:
    """`rec_opts` are passed to `recommend.for_run` (traffic, rack budget, tariff).

    `physical` is `(fleet_audit, gpu)` from the rack photo audit. A GPU with no unit seen in
    its rack photo is refused (`NotVerified`) before any cap is written. Otherwise the guard's
    highest cap is tightened to the rack's per-node ceiling, the ceiling goes into what the
    planner sees, and the rack's real node count and budget go into the recommendations.
    """

    lim = rack = None
    if physical is not None:
        fleet, gpu = physical
        lim = fleet.require(gpu)  # raises NotVerified
        rack = fleet.rack_of(gpu)
        guard.tighten_max(lim.ceiling_w)
        rec_opts = {**rack.recommend_opts(), **(rec_opts or {})}
    phys_summary = rack.summary() if rack is not None else None
    cap = guard.cap_w()
    start_cap = cap
    knobs = dict(knobs)
    current = baseline
    out = LoopResult(cap, knobs)
    stop = False
    if lim is not None and cap > lim.ceiling_w:
        # The rack cannot feed every node at this cap. Step to the ceiling first, through the
        # same guard, trial and gate as any other step, so a failure reverts like any other.
        try_cap = guard.set_cap_w(lim.ceiling_w)
        result = trial(try_cap, dict(knobs))
        verdict = gate(result)
        out.physical = {"cap_w": try_cap, "from_cap_w": cap, "passed": bool(verdict.passed),
                        "failed": list(verdict.failed), "rack": lim.rack,
                        "reason": f"rack {lim.rack} ceiling {lim.ceiling_w} W"}
        if verdict.passed:
            cap, current = try_cap, result
            if emit is not None:
                emit.emit("action.applied", {"change": {"power_cap_w": cap},
                                             "reason": out.physical["reason"]},
                          step_id="plan:physical")
        else:
            guard.set_cap_w(cap, restoring=True)  # the start state, even if above the ceiling
            stop = True  # the ceiling breaks the p99 or quality gate: nothing to tune
            if emit is not None:
                emit.emit("action.rolled_back", {"change": {"power_cap_w": try_cap},
                                                 "restored_cap_w": cap,
                                                 "failed_gates": out.physical["failed"]},
                          step_id="plan:physical")
    for n in range(0 if stop else max_steps):
        summary = summarize(cap_w=cap, min_w=guard.min_w, max_w=guard.top_w, step_w=step_w,
                            base_p99_s=baseline.p99_s, p99_s=current.p99_s, slack=slack,
                            wh_per_req=current.wh_per_req,
                            base_wh_per_req=baseline.wh_per_req, gates=out.gates,
                            knobs=knobs, physical=phys_summary)
        d = maker.decide(summary, step_id=f"plan:{n}")
        out.decisions.append(d)
        c = d.choice
        if c.kind == "stop":
            break
        try_cap, try_knobs = cap, dict(knobs)
        if c.kind == "cap":
            try_cap = guard.set_cap_w(c.power_cap_w)
        else:
            try_knobs[c.knob] = c.value
        result = trial(try_cap, try_knobs)
        verdict = gate(result)
        record = {"cap_w": try_cap if c.kind == "cap" else None,
                  "knob": c.knob, "passed": verdict.passed, "failed": list(verdict.failed)}
        out.gates.append(record)
        step = f"plan:{n}"
        if verdict.passed:
            cap, knobs, current = try_cap, try_knobs, result
            if emit is not None:
                emit.emit("action.applied", {"change": {"power_cap_w": cap, **(
                    {c.knob: c.value} if c.kind == "knob" else {})}}, step_id=step)
        else:
            guard.set_cap_w(cap)
            if emit is not None:
                emit.emit("action.rolled_back", {"change": {"power_cap_w": try_cap},
                                                 "restored_cap_w": cap,
                                                 "failed_gates": record["failed"]},
                          step_id=step)
    rec = recommend.for_run(start_cap_w=start_cap, final_cap_w=cap,
                            gate_passed=any(g["passed"] for g in out.gates)
                            or bool(out.physical and out.physical["passed"]),
                            base_wh_per_req=baseline.wh_per_req,
                            final_wh_per_req=current.wh_per_req,
                            base_p99_ms=baseline.p99_s * 1000, final_p99_ms=current.p99_s * 1000,
                            knob=recommend.spark_knob(out.decisions, out.gates),
                            **(rec_opts or {}))
    if rack is not None:
        extra = rack.recommend_items(gpu, start_cap_w=start_cap)
        if out.physical and not out.physical["passed"]:
            extra.append(recommend.Item(
                recommend.ADVICE, "capping to the rack ceiling broke a quality or p99 gate: "
                "this rack has more nodes than its power budget can serve at this target; "
                "remove nodes or raise the budget", "physical_ceiling_gate_failed", "failed"))
        rec = recommend.add_actions(rec, extra)
    out.recommendations = recommend.with_summary(rec, summarizer)
    recommend.emit(emit, out.recommendations)
    out.cap_w, out.knobs = cap, knobs
    return out
