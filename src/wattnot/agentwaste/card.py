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
