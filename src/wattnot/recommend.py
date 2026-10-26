"""What a run means for the data centre: where it applies and what to do next.

Runs at the end of every power-cap run, planned run and `wattnot impact`.
Everything here is plain rules on this run's numbers. Same input, same output.

Labels never lie:

* MEASURED: read from this run (caps set on the driver, Wh/request, p99).
* ADVICE: a step with no number in it (re-verify after changes).
* ESTIMATE: anything scaled or assumed (rack totals, extra GPUs, dollars,
  water). Water is always ESTIMATE. Money is always USD.

If the gate never passed below the starting cap, or the saving is below
`MIN_SAVING_PCT`, the answer is "no change recommended".
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace

from wattnot import impact as _impact

MIN_SAVING_PCT = 2.0  # below this, a change is not worth the risk
GPUS_PER_RACK = 8  # assumption, e.g. one 8-GPU server per rack budget
NO_CHANGE = "no change recommended"

MEASURED, ESTIMATE = "MEASURED", "ESTIMATE"
SIMULATED = "SIMULATED"  # fake backend: shaped like MEASURED, but no GPU was read
ADVICE = "ADVICE"  # no number in it, so neither MEASURED nor ESTIMATE


@dataclass(frozen=True)
class Item:
    label: str  # MEASURED | ESTIMATE, or ADVICE when the item carries no number
    text: str
    key: str = ""
    value: float | int | str | None = None
    unit: str | None = None

    def line(self) -> str:
        return f"{self.label} {self.text}"


@dataclass(frozen=True)
class Recommendations:
    verdict: str  # "change recommended" | NO_CHANGE
    applies: tuple[str, ...]
    actions: tuple[Item, ...]
    assumptions: dict = field(default_factory=dict)
    summary: str = ""
    summary_model: str = "rules"

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, "applies": list(self.applies),
                "actions": [asdict(a) for a in self.actions],
                "assumptions": dict(self.assumptions),
                "summary": self.summary, "summary_model": self.summary_model}

    def lines(self) -> list[str]:
        out = ["What this means for your data centre", f"Verdict: {self.verdict}",
               "Where this applies:"]
        out += [f"  - {a}" for a in self.applies]
        out.append("Recommended actions:")
        out += [f"  - {a.line()}" for a in self.actions]
        if self.summary:
            out.append(f"Summary ({self.summary_model}): {self.summary}")
        return out


def _applies(*, cap_freed_w: float, kwh: float, litres: float, always_on: bool,
             changed: bool) -> tuple[str, ...]:
    out = []
    if always_on:
        out.append("always-on inference serving (chat, search, agents) where every "
                   "request pays the energy cost")
    out.append("overnight batch or eval jobs, where a lower cap rarely hurts deadlines")
    if cap_freed_w > 0:
        out.append("racks near their breaker or power limit: a lower cap frees power "
                   "for more GPUs")
        out.append("sites with tight power contracts or demand charges")
    if litres > 0:
        out.append("water-stressed sites that use evaporative cooling")
    out.append("EU efficiency reporting (Energy Efficiency Directive): the signed "
               "receipt documents the Wh/request change" if changed else
               "EU efficiency reporting: this run is a measured baseline")
    return tuple(out)


def _money_water(i: _impact.Impact, days: float, gpus: int) -> list[Item]:
    return [
        Item(ESTIMATE, f"saves about ${i.usd:,.2f} per {days:g} days across {gpus} GPU(s) "
             f"({i.kwh:,.1f} kWh)", "usd_saved", round(i.usd, 2), "USD"),
        Item(ESTIMATE, f"saves about {i.litres:,.0f} L of cooling water per {days:g} days",
             "water_saved_l", round(i.litres), "L"),
    ]


def _reverify() -> Item:
    return Item(ADVICE, "re-run WattNot after any model, quantization, driver or "
                "traffic change; the cap is only proven for this workload", "reverify")


def _for_run(*, start_cap_w: int, final_cap_w: int, gate_passed: bool,
            base_wh_per_req: float, final_wh_per_req: float,
            base_p99_ms: float | None = None, final_p99_ms: float | None = None,
            requests_per_gpu_per_day: float = 100_000, gpus: int = GPUS_PER_RACK,
            gpus_per_rack: int = GPUS_PER_RACK, rack_budget_w: int | None = None,
            knob: Mapping | None = None,
            tariff_usd_per_kwh: float = _impact.TARIFF_USD_PER_KWH,
            wue_l_per_kwh: float = _impact.WUE_L_PER_KWH, pue: float = _impact.PUE,
            days: float = _impact.DAYS, always_on: bool = True) -> Recommendations:
    """Recommendations for a power-cap or planned run.

    `knob` is an optional Muse Spark knob suggestion: {knob, value, applied}.
    `rack_budget_w` defaults to `gpus_per_rack * start_cap_w` (GPU power only).
    """

    if gpus_per_rack < 1 or start_cap_w <= 0 or final_cap_w <= 0 or base_wh_per_req <= 0:
        raise ValueError("caps, gpus_per_rack and baseline Wh/request must be positive")
    budget = rack_budget_w if rack_budget_w is not None else gpus_per_rack * start_cap_w
    wh_delta = base_wh_per_req - final_wh_per_req
    saving_pct = 100 * wh_delta / base_wh_per_req
    freed = start_cap_w - final_cap_w
    changed = gate_passed and final_cap_w < start_cap_w and saving_pct >= MIN_SAVING_PCT
    assumptions = {"requests_per_gpu_per_day": requests_per_gpu_per_day, "gpus": gpus,
                   "gpus_per_rack": gpus_per_rack, "rack_budget_w": budget,
                   "rack_budget_note": "GPU power only, assumed" if rack_budget_w is None
                   else "GPU power only, given", "tariff_usd_per_kwh": tariff_usd_per_kwh,
                   "wue_l_per_kwh": wue_l_per_kwh, "pue": pue, "days": days,
                   "min_saving_pct": MIN_SAVING_PCT}
    acts: list[Item] = []
    if not changed:
        why = ("the gate failed at the first step below the starting cap"
               if not gate_passed or final_cap_w >= start_cap_w else
               f"the saving is only {saving_pct:.1f}% (under {MIN_SAVING_PCT:g}%)")
        acts.append(Item(MEASURED, f"{NO_CHANGE}: keep the cap at {start_cap_w} W; {why}",
                         "power_cap_w", start_cap_w, "W"))
        acts += _knob_items(knob)
        acts.append(_reverify())
        return Recommendations(NO_CHANGE, _applies(cap_freed_w=0, kwh=0, litres=0,
                                                   always_on=always_on, changed=False),
                               tuple(acts), assumptions)

    p99 = (f"; p99 {base_p99_ms:.0f} -> {final_p99_ms:.0f} ms"
           if base_p99_ms is not None and final_p99_ms is not None else "")
    acts.append(Item(MEASURED, f"set the power cap to {final_cap_w} W (from {start_cap_w} W). "
                     "It is the last cap that passed the gate: quality within 1 point and "
                     f"p99 within +10%{p99}", "power_cap_w", final_cap_w, "W"))
    acts.append(Item(MEASURED, f"energy per request {base_wh_per_req:.4f} -> "
                     f"{final_wh_per_req:.4f} Wh ({saving_pct:.1f}% less)",
                     "wh_saved_pct", round(saving_pct, 1), "%"))
    acts.append(Item(MEASURED, f"rack headroom freed: {freed} W per GPU (cap limit, not "
                     "average draw)", "headroom_w_per_gpu", freed, "W"))
    acts.append(Item(ESTIMATE, f"rack headroom freed: {freed * gpus_per_rack:,} W per rack "
                     f"of {gpus_per_rack} GPUs", "headroom_w_per_rack",
                     freed * gpus_per_rack, "W"))
    extra = budget // final_cap_w - budget // start_cap_w
    acts.append(Item(ESTIMATE, f"{extra} extra GPU(s) fit under the same rack budget of "
                     f"{budget:,} W ({assumptions['rack_budget_note']}: "
                     f"{budget:,} // {final_cap_w} - {budget:,} // {start_cap_w})",
                     "extra_gpus_per_rack", extra, "GPUs"))
    i = _impact.estimate(wh_delta, requests_per_gpu_per_day, gpus,
                         tariff_usd_per_kwh=tariff_usd_per_kwh, wue_l_per_kwh=wue_l_per_kwh,
                         pue=pue, days=days)
    acts += _money_water(i, days, gpus)
    acts += _knob_items(knob)
    acts.append(_reverify())
    return Recommendations("change recommended",
                           _applies(cap_freed_w=freed, kwh=i.kwh, litres=i.litres,
                                    always_on=always_on, changed=True),
                           tuple(acts), assumptions)


def for_run(*, simulated: bool = False, **kw) -> Recommendations:
    """See `_for_run`. With `simulated=True` (fake backend) no item says MEASURED."""

    rec = _for_run(**kw)
    if not simulated:
        return rec
    acts = tuple(replace(a, label=SIMULATED) if a.label == MEASURED else a for a in rec.actions)
    return replace(rec, actions=acts)


def _knob_items(knob: Mapping | None) -> list[Item]:
    if not knob:
        return []
    name, value = knob["knob"], knob["value"]
    if knob.get("applied"):
        return [Item(MEASURED, f"keep vLLM {name}={value}: Muse Spark suggested it and it "
                     "passed the gate", "knob", f"{name}={value}")]
    return [Item(ESTIMATE, f"try vLLM {name}={value} next (Muse Spark suggestion, "
                 "untested here); run it through the gate first", "knob", f"{name}={value}")]


def for_impact(i: _impact.Impact, *, gpus: int, days: float) -> Recommendations:
    """Recommendations for `wattnot impact` (no cap data, so money and water only)."""

    if i.kwh <= 0:
        acts = (Item(ESTIMATE, f"{NO_CHANGE}: the measured change adds energy",
                     "usd_saved", 0, "USD"), _reverify())
        return Recommendations(NO_CHANGE, _applies(cap_freed_w=0, kwh=0, litres=0,
                                                   always_on=True, changed=False), acts)
    acts = tuple(_money_water(i, days, gpus)) + (_reverify(),)
    return Recommendations("change recommended",
                           _applies(cap_freed_w=0, kwh=i.kwh, litres=i.litres,
                                    always_on=True, changed=True), acts)


def spark_knob(decisions: Sequence, gates: Sequence[Mapping]) -> dict | None:
    """The last knob Muse Spark (not the fallback) chose, and whether it passed."""

    for n in range(len(decisions) - 1, -1, -1):
        d = decisions[n]
        if d.model != "fallback" and d.choice.kind == "knob":
            applied = n < len(gates) and gates[n].get("knob") == d.choice.knob \
                and gates[n]["passed"]
            return {"knob": d.choice.knob, "value": d.choice.value, "applied": applied}
    return None


Summarizer = Callable[[dict], str]


def with_summary(rec: Recommendations, summarizer: Summarizer | None) -> Recommendations:
    """Optional one-line summary. The summarizer sees numbers only; any failure
    keeps the rule-based summary."""

    nums = {a.key: a.value for a in rec.actions
            if a.key and isinstance(a.value, (int, float))}
    rule = (f"{rec.verdict}; " + ", ".join(f"{k}={v}" for k, v in nums.items())).strip("; ")
    if summarizer is not None:
        try:
            text = " ".join(str(summarizer({"verdict": rec.verdict, **nums})).split())[:200]
            if text:
                return _replace(rec, text, "muse-spark-1.3")
        except Exception:  # fall back to the rule line
            pass
    return _replace(rec, rule, "rules")


def _replace(rec: Recommendations, summary: str, model: str) -> Recommendations:
    return Recommendations(rec.verdict, rec.applies, rec.actions, rec.assumptions,
                           summary, model)


def emit(emitter, rec: Recommendations, step_id: str = "recommendations") -> None:
    if emitter is not None:
        emitter.emit("recommendations", rec.to_dict(), step_id=step_id)
