"""The Agent waste results card: a trimmed view of the benchmark for the dashboard.

Every number shown carries one label: MEASURED, ESTIMATE, SIMULATED, MOCK,
REPLAY or ADVICE. A missing value is None (shown as a dash), never invented.
"""

from __future__ import annotations

import json
from pathlib import Path

SCHEMA = "wattnot.agent-waste/1"
PARTNER = {"id": "meta", "name": "Meta Muse Spark"}

LABELS = {"calls": "MEASURED", "tokens": "MEASURED", "wall": "MEASURED",
          "energy": "ESTIMATE", "cost": "ESTIMATE"}


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_results(path: str | Path | None = None) -> dict | None:
    """Read the benchmark results file. None when missing or not valid JSON."""
    p = Path(path) if path is not None else _root() / "data" / "agent-waste" / "results.json"
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("schema") != SCHEMA:
        return None
    return d


V2_SCHEMA = "wattnot.agent-waste-v2/1"
TITLES = {
    "fuzzy-1-prompt-drift": "The stop word never arrives",
    "retry-1-status-500": "A tool keeps failing",
    "sprawl-2-redelegate-failure": "Helpers retry what already failed",
}
CATEGORY_NAMES = {"normal": "Normal tasks", "fuzzy": "Stop word never arrives",
                  "retry": "Tool retry storms", "noprogress": "No-progress loops",
                  "sprawl": "Helper sprawl"}


def load_v2(path: str | Path | None = None) -> dict | None:
    """Read the second-layer results file. None when missing, not JSON or another schema."""
    p = Path(path) if path is not None else _root() / "data" / "agent-waste" / "v2" / "results.json"
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("schema") != V2_SCHEMA:
        return None
    return d


def _vrow(block: dict) -> dict:
    """One comparison table: harness alone vs harness + saver, with a label on every number."""
    h, s = block.get("harness", {}), block.get("harness+saver", {})
    d = block.get("saver_vs_harness", {})

    def per_run(arm: dict, key: str, total: str, nd: int):
        if arm.get(key) is not None:
            return arm[key]
        if arm.get("runs") and arm.get(total) is not None:
            return round(arm[total] / arm["runs"], nd)
        return None

    return {
        "runs": h.get("runs") or s.get("runs") or 0,
        "rows": {
            "calls per run": _row(h.get("calls_per_run"), s.get("calls_per_run"),
                                  d.get("calls"), "MEASURED"),
            "tokens per run": _row(per_run(h, "tokens_per_run", "total_tokens", 0),
                                   per_run(s, "tokens_per_run", "total_tokens", 0),
                                   d.get("total_tokens"), "MEASURED"),
            "wall time per run (s)": _row(h.get("wall_s_per_run"), s.get("wall_s_per_run"),
                                          d.get("wall_s_per_run"), "MEASURED"),
            "energy per run (J)": _row(per_run(h, "joules_per_run", "joules", 1),
                                       per_run(s, "joules_per_run", "joules", 1),
                                       d.get("joules"), "ESTIMATE"),
            "cost per run ($)": _row(per_run(h, "usd_per_run", "usd", 5),
                                     per_run(s, "usd_per_run", "usd", 5),
                                     d.get("usd"), "ESTIMATE"),
        },
        "success": {"off": h.get("success"), "on": s.get("success"),
                    "off_pct": h.get("success_pct"), "on_pct": s.get("success_pct")},
        "miss_runs": {"off": h.get("miss_runs"), "on": s.get("miss_runs")},
    }


def card_v2(res: dict) -> dict:
    """The second-layer view: the saver behind real harness stop rules."""
    normal, inj = _vrow(res.get("normal", {})), _vrow(res.get("injected", {}))
    cats = []
    for cid, name in CATEGORY_NAMES.items():
        b = res.get("by_category", {}).get(cid)
        if not b or cid == "normal":
            continue
        v = _vrow(b)
        cats.append({"id": cid, "name": name, "runs": v["runs"],
                     "calls_off": v["rows"]["calls per run"]["off"],
                     "calls_on": v["rows"]["calls per run"]["on"],
                     "tokens_change_pct": v["rows"]["tokens per run"]["change_pct"],
                     "success": v["success"], "miss_runs": v["miss_runs"]})
    scenarios = []
    for sc in res.get("scenarios", []):
        scenarios.append({**sc, "title": TITLES.get(sc.get("case"), sc.get("case"))})
    n_tok = normal["rows"]["tokens per run"]["change_pct"]
    i_tok = inj["rows"]["tokens per run"]["change_pct"]
    def sg(v):
        return "n/a" if v is None else f"{v:+.1f}%"

    headline = (
        f"Behind three harness loops that keep their own stop rules, the saver changed tokens "
        f"per task by {sg(n_tok)} on normal tasks (a tie) and by {sg(i_tok)} on injected cases "
        f"where the harness stop misses.")
    notes = [
        "The three loops are our own minimal versions of common patterns (a ReAct loop with an "
        "iteration cap, a stop-marker loop, a planner with helper agents). They are not the "
        "real libraries.",
        "The failures are injected on purpose; the model's behaviour inside them is real "
        "(Muse Spark). The claim is about these cases, not about all agents.",
        "Calls, tokens, time, success and misses are MEASURED. Energy and dollars are ESTIMATE, "
        "with the same rates as the first benchmark.",
    ]
    return {"headline": headline, "normal": normal, "injected": inj, "categories": cats,
            "scenarios": scenarios, "notes": notes, "caps": res.get("caps", {}),
            "reps": res.get("reps"),
            "cases": sum(1 for c in res.get("cases", {}).values() if c.get("category") != "normal"),
            "normal_cases": sum(1 for c in res.get("cases", {}).values()
                                if c.get("category") == "normal"),
            "spend_usd": res.get("spend_usd_own"), "generated": res.get("generated")}


V3_SCHEMA = "wattnot.agent-waste-v3/1"


def load_v3(path: str | Path | None = None) -> dict | None:
    """Read the v3 (after-the-fixes) results file. None when missing or another schema."""
    p = Path(path) if path is not None else _root() / "data" / "agent-waste" / "v3" / "results.json"
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("schema") != V3_SCHEMA:
        return None
    return d


def _arm_stats(arm: dict) -> dict:
    """Per-run numbers for one arm of one block."""
    n = arm.get("runs") or 0

    def per(key: str, nd: int):
        return round(arm[key] / n, nd) if n and arm.get(key) is not None else None

    return {"runs": n, "success": arm.get("success"), "success_pct": arm.get("success_pct"),
            "calls": arm.get("calls_per_run"), "tokens": arm.get("tokens_per_run"),
            "wall_s": arm.get("wall_s_per_run"), "usd": per("usd", 5), "joules": per("joules", 1),
            "miss_runs": arm.get("miss_runs")}


def _v3_block(block: dict, before: dict) -> dict:
    return {"harness": _arm_stats(block.get("harness", {})),
            "saver_v3": _arm_stats(block.get("harness+saver", {})),
            "harness_v2": _arm_stats(before.get("harness", {})),
            "saver_v2": _arm_stats(before.get("harness+saver", {}))}


def spread(rows: list[dict]) -> list[dict]:
    """Per case and arm: mean, stdev, min and max of calls and tokens, and passes, over reps."""
    import statistics as st

    def stats(xs: list[float]) -> dict:
        return {"mean": round(st.mean(xs), 2), "sd": round(st.stdev(xs), 2) if len(xs) > 1 else 0.0,
                "min": min(xs), "max": max(xs), "median": st.median(xs)}

    out = []
    for cid in sorted({r["case"] for r in rows if not r.get("error")}):
        arms = {}
        for arm in ("harness", "harness+saver"):
            xs = [r for r in rows if r["case"] == cid and r["arm"] == arm and not r.get("error")]
            if not xs:
                continue
            arms[arm] = {"runs": len(xs), "success": sum(bool(r["success"]) for r in xs),
                         "miss_runs": sum(bool(r.get("miss")) for r in xs),
                         "calls": stats([r["calls"] for r in xs]),
                         "tokens": stats([r["total_tokens"] for r in xs])}
        out.append({"id": cid, "arms": arms})
    return out


def load_repeat(path: str | Path | None = None) -> dict | None:
    """The 6-rep sprawl re-run (same v3 code). None when missing or without a spread table."""
    p = (Path(path) if path is not None
         else _root() / "data" / "agent-waste" / "sprawl6" / "results.json")
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("spread"), list):
        return None
    try:
        v = json.loads((p.parent / "verdicts.json").read_text())
    except (OSError, ValueError):
        v = {}
    return {"reps": d.get("reps"), "spread": d["spread"], "verdicts": v.get("verdicts", {}),
            "verdict_label": v.get("label"), "notes": v.get("notes", []),
            "spend_usd": d.get("spend_usd_own")}


def card_v3(res: dict) -> dict:
    """What changed after the two fixes: the same cases, v2 saver vs v3 saver, side by side."""
    before = res.get("before", {})
    cases = []
    for cid, c in res.get("cases", {}).items():
        if c.get("category") == "normal":
            continue
        old = before.get("cases", {}).get(cid, {})
        cases.append({"id": cid, "category": c.get("category"),
                      **_v3_block(c, old)})
    fixed = _v3_block(res.get("injected", {}), before.get("injected", {}))
    normal = _v3_block(res.get("normal", {}), before.get("normal", {}))
    return {"fixes": res.get("fixes", []), "fixed": fixed, "normal": normal, "cases": cases,
            "notes": res.get("notes", []), "reps": res.get("reps"),
            "spend_usd": res.get("spend_usd_own"), "generated": res.get("generated")}


def _row(off, on, change_pct, tag) -> dict:
    return {"off": off, "on": on, "change_pct": change_pct, "tag": tag}


def card(results: dict) -> dict:
    """A trimmed view of the benchmark: headline, rows, success, groups, reference."""
    arms = results.get("arms", {})
    off = arms.get("loop-off", {}).get("all", {})
    on = arms.get("loop-on", {}).get("all", {})
    auto = arms.get("auto-ref", {}).get("all", {})
    delta = results.get("on_vs_off", {}).get("all", {})
    runs = on.get("runs") or off.get("runs") or 0

    off_calls = off.get("calls_per_run")
    on_calls = on.get("calls_per_run")
    on_ok = on.get("success")
    headline = (
        f"Without a stop action an agent loop ran {off_calls} calls per task; "
        f"with the saver it ran {on_calls}, and {on_ok} of {runs} tasks still passed."
    )

    def per_run(arm: dict, key: str, total: str, nd: int = 2):
        if arm.get(key) is not None:
            return arm[key]
        if arm.get("runs") and arm.get(total) is not None:
            return round(arm[total] / arm["runs"], nd)
        return None

    rows = {
        "calls per run": _row(off_calls, on_calls, delta.get("calls"), LABELS["calls"]),
        "tokens per run": _row(per_run(off, "tokens_per_run", "total_tokens", 0),
                               per_run(on, "tokens_per_run", "total_tokens", 0),
                               delta.get("total_tokens"), LABELS["tokens"]),
        "wall time per run (s)": _row(off.get("wall_s_per_run"), on.get("wall_s_per_run"),
                                     delta.get("wall_s_per_run"), LABELS["wall"]),
        "energy per run (J)": _row(per_run(off, "joules_per_run", "joules", 1),
                                  per_run(on, "joules_per_run", "joules", 1),
                                  delta.get("joules"), LABELS["energy"]),
        "cost per run ($)": _row(per_run(off, "usd_per_run", "usd", 5),
                                per_run(on, "usd_per_run", "usd", 5),
                                delta.get("usd"), LABELS["cost"]),
    }

    success = {"off_pct": off.get("success_pct"), "on_pct": on.get("success_pct"),
               "auto_pct": auto.get("success_pct"), "runs_per_arm": runs or None}

    groups = {}
    for g in ("work", "early", "no_work"):
        g_off = arms.get("loop-off", {}).get(g, {})
        g_on = arms.get("loop-on", {}).get(g, {})
        groups[g] = {"calls_off": g_off.get("calls_per_run"), "calls_on": g_on.get("calls_per_run"),
                     "success_off": g_off.get("success_pct"), "success_on": g_on.get("success_pct")}

    reference = {"calls_per_run": auto.get("calls_per_run"), "success_pct": auto.get("success_pct")}

    notes = [
        "Three arms: a loop with no stop action, the same loop with the saver, "
        "and a standard harness that already stops by itself.",
        "Energy and dollars are ESTIMATE: they use RUN2's measured joules per token "
        "for Llama 3.1 8B as a stand-in rate.",
        "Tokens, calls, time and success are MEASURED on Muse Spark.",
    ]
    return {"partner": dict(PARTNER), "headline": headline, "rows": rows, "success": success,
            "groups": groups, "reference": reference, "notes": notes,
            "spend_usd": results.get("spend_usd_ledger"), "generated": results.get("generated")}
