"""Turn benchmark runs into the numbers the docs and the dashboard card show.

MEASURED: calls, tokens (billed by the upstream), wall time, task success, early exits.
ESTIMATE: energy and dollars. Energy applies RUN2's measured joules per generated token for
Llama 3.1 8B on one H100 to the completion tokens of this run. Muse Spark is a different, larger
model, so this is a stand-in rate, not a measurement of Muse Spark. Dollars use the API's
published Muse Spark price on the billed tokens.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path

from wattnot.meta.ledger import cost_usd

# data/h100-run2/vm/baseline.json: joules / tokens at the stock 700 W cap, 222 requests.
RUN2_J_PER_TOKEN = 0.14966580932784637
RUN2_SOURCE = "data/h100-run2/vm/baseline.json (Llama-3.1-8B-Instruct, one H100, stock 700 W)"
PRICE_SOURCE = "https://dev.meta.ai/docs/pricing-rate-limits (muse-spark-1.3, standard rates)"
MODEL = "muse-spark-1.3"
GROUPS = ("work", "early", "no_work")


def j_per_token(root: str | Path | None = None) -> float:
    """RUN2's measured value when the data folder is present, else the pinned constant."""
    p = Path(root or Path(__file__).resolve().parents[4]) / "data/h100-run2/vm/baseline.json"
    try:
        d = json.loads(p.read_text())
        return float(d["joules"]) / float(d["tokens"])
    except (OSError, KeyError, ValueError, ZeroDivisionError):
        return RUN2_J_PER_TOKEN


def run_usd(r: Mapping) -> float:
    return cost_usd(MODEL, r["prompt_tokens"], r["completion_tokens"], r.get("cached_tokens", 0))


def _agg(rows: list[Mapping], jpt: float) -> dict:
    n = len(rows)
    if not n:
        return {"runs": 0}
    s = lambda k: sum(r[k] for r in rows)  # noqa: E731
    usd = sum(run_usd(r) for r in rows)
    return {
        "runs": n,
        "success": sum(1 for r in rows if r["success"]),
        "success_pct": round(100 * sum(1 for r in rows if r["success"]) / n, 1),
        "calls": s("calls"), "calls_per_run": round(s("calls") / n, 2),
        "prompt_tokens": s("prompt_tokens"), "completion_tokens": s("completion_tokens"),
        "reasoning_tokens": s("reasoning_tokens"), "total_tokens": s("total_tokens"),
        "tokens_per_run": round(s("total_tokens") / n),
        "tool_calls": s("tool_calls"), "mutations": s("mutations"),
        "wall_s_per_run": round(s("wall_s") / n, 2),
        "usd": round(usd, 4), "usd_per_run": round(usd / n, 5),
        "joules": round(s("completion_tokens") * jpt, 1),
        "ended_by": {k: sum(1 for r in rows if r["ended_by"] == k)
                     for k in sorted({r["ended_by"] for r in rows})},
    }


def _pct(new: float, old: float) -> float | None:
    return round(100 * (new - old) / old, 1) if old else None


def summarize(runs: Iterable[Mapping], jpt: float | None = None) -> dict:
    runs = [dict(r) if "total_tokens" in r else {**r, "total_tokens":
            r["prompt_tokens"] + r["completion_tokens"]} for r in runs]
    jpt = j_per_token() if jpt is None else jpt
    arms = sorted({r["arm"] for r in runs})
    by: dict[str, dict] = {}
    for arm in arms:
        rows = [r for r in runs if r["arm"] == arm]
        by[arm] = {"all": _agg(rows, jpt),
                   **{g: _agg([r for r in rows if r["group"] == g], jpt) for g in GROUPS}}

    def delta(a: str, b: str) -> dict:
        out = {}
        for g in ("all", *GROUPS):
            x, y = by.get(a, {}).get(g, {}), by.get(b, {}).get(g, {})
            if not x.get("runs") or not y.get("runs"):
                continue
            out[g] = {k: _pct(x[k], y[k]) for k in
                      ("calls", "total_tokens", "completion_tokens", "wall_s_per_run",
                       "usd", "joules")}
            out[g]["success_points"] = round(x["success_pct"] - y["success_pct"], 1)
        return out

    flips = defaultdict(dict)
    for r in runs:
        flips[(r["task"], r.get("rep", 0))][r["arm"]] = r["success"]
    changed = sorted(f"{t}#{rep}" for (t, rep), v in flips.items()
                     if v.get("loop-on") is not None and v.get("loop-off") is not None
                     and v["loop-on"] != v["loop-off"])
    return {
        "model": MODEL, "j_per_token": round(jpt, 6), "j_per_token_source": RUN2_SOURCE,
        "price_source": PRICE_SOURCE,
        "labels": {"calls": "MEASURED", "tokens": "MEASURED", "wall_time": "MEASURED",
                   "success": "MEASURED", "energy_j": "ESTIMATE", "usd": "ESTIMATE"},
        "arms": by,
        "on_vs_off": delta("loop-on", "loop-off"),
        "on_vs_auto": delta("loop-on", "auto-ref"),
        "auto_vs_off": delta("auto-ref", "loop-off"),
        "outcome_differs_on_vs_off": changed,
    }
