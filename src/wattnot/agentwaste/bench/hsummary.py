"""Summary for the second-layer benchmark (harness alone vs harness + saver).

MEASURED: calls, tokens (billed by the upstream), wall time, success, whether the harness's own
stop rule missed. ESTIMATE: energy and dollars, with the same rates as the first benchmark
(see summary.py).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .cases import DEMO_IDS, INJECTED, Case
from .summary import MODEL, PRICE_SOURCE, RUN2_SOURCE, _agg, _pct, j_per_token

SCHEMA = "wattnot.agent-waste-v2/1"
ARMS = ("harness", "harness+saver")
CATEGORIES = ("normal", "fuzzy", "retry", "noprogress", "sprawl")
CASE_BY_ID = {c.id: c for c in INJECTED}


def _delta(on: Mapping, off: Mapping) -> dict:
    if not on.get("runs") or not off.get("runs"):
        return {}
    out = {k: _pct(on[k], off[k]) for k in ("calls", "total_tokens", "completion_tokens",
                                            "wall_s_per_run", "usd", "joules")}
    out["success_points"] = round(on["success_pct"] - off["success_pct"], 1)
    return out


def _block(rows: list[Mapping], jpt: float) -> dict:
    by = {a: _agg([r for r in rows if r["arm"] == a], jpt) for a in ARMS}
    for a in ARMS:
        n = by[a].get("runs", 0)
        by[a]["miss_runs"] = sum(1 for r in rows if r["arm"] == a and r["miss"])
        by[a]["nudges"] = sum(r["nudges"] for r in rows if r["arm"] == a)
        by[a]["helpers"] = sum(r["helpers"] for r in rows if r["arm"] == a)
        by[a]["errors"] = sum(1 for r in rows if r["arm"] == a and r["error"])
        by[a]["n"] = n
    return {**by, "saver_vs_harness": _delta(by["harness+saver"], by["harness"])}


def pick_scenario(rows: list[Mapping], case_id: str) -> dict | None:
    """The replayable demo for one case. The rule, fixed before the run: use the first rep in
    which the harness alone missed; if it never did, rep 0. Nothing is cherry-picked by outcome
    of the saver."""
    mine = [r for r in rows if r["case"] == case_id]
    if not mine:
        return None
    reps = sorted({r["rep"] for r in mine})
    chosen = next((rep for rep in reps if any(r["arm"] == "harness" and r["rep"] == rep
                                              and r["miss"] for r in mine)), reps[0])
    pair = {r["arm"]: r for r in mine if r["rep"] == chosen}
    case: Case | None = CASE_BY_ID.get(case_id)
    out = {"case": case_id, "rep": chosen, "category": mine[0]["category"],
           "harness": mine[0]["harness"], "prompt": case.prompt if case else "",
           "injected": case.injected if case else "", "arms": {}}
    for arm, r in pair.items():
        out["arms"][arm] = {k: r[k] for k in ("success", "calls", "total_tokens", "wall_s",
                                              "ended_by", "stop_reason", "miss", "helpers")}
        out["arms"][arm]["trace"] = r["trace"]
    return out


def summarize(runs: Iterable[Mapping], jpt: float | None = None) -> dict:
    rows = [dict(r) for r in runs]
    jpt = j_per_token() if jpt is None else jpt
    injected_rows = [r for r in rows if r["category"] != "normal"]
    cats = {c: _block([r for r in rows if r["category"] == c], jpt) for c in CATEGORIES
            if any(r["category"] == c for r in rows)}
    harnesses = {h: _block([r for r in rows if r["category"] == "normal" and r["harness"] == h],
                           jpt)
                 for h in sorted({r["harness"] for r in rows if r["category"] == "normal"})}
    cases = {}
    for cid in sorted({r["case"] for r in rows}):
        mine = [r for r in rows if r["case"] == cid]
        cases[cid] = {"category": mine[0]["category"], "harness": mine[0]["harness"],
                      "injected": CASE_BY_ID[cid].injected if cid in CASE_BY_ID else "",
                      **_block(mine, jpt)}
    flips = sorted(f"{r['case']}#{r['rep']}" for r in rows if r["arm"] == "harness+saver"
                   and any(o["case"] == r["case"] and o["rep"] == r["rep"]
                           and o["arm"] == "harness" and o["success"] != r["success"]
                           for o in rows))
    scenarios = [s for s in (pick_scenario(rows, i) for i in DEMO_IDS) if s]
    return {
        "schema": SCHEMA, "model": MODEL, "j_per_token": round(jpt, 6),
        "j_per_token_source": RUN2_SOURCE, "price_source": PRICE_SOURCE,
        "labels": {"calls": "MEASURED", "tokens": "MEASURED", "wall_time": "MEASURED",
                   "success": "MEASURED", "miss": "MEASURED", "energy_j": "ESTIMATE",
                   "usd": "ESTIMATE"},
        "normal": _block([r for r in rows if r["category"] == "normal"], jpt),
        "injected": _block(injected_rows, jpt),
        "by_category": cats, "by_harness_normal": harnesses, "cases": cases,
        "outcome_differs": flips, "scenarios": scenarios,
    }
