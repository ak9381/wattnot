"""Muse Spark's planner calls as a readable timeline.

Read from a run's event log: for each planner call, what Spark was shown (a short summary),
what it chose, its one-line reasoning, how long the call took and what it cost, plus the
gate's verdict on that choice. This is how a judge watches Spark find 350 W.
Latency and tokens are MEASURED (from the call); dollars are tokens times the published
price, so they are an ESTIMATE.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path

from wattnot.meta.ledger import price_for


def _usd(model: str, tok: Mapping) -> float | None:
    try:
        p, _ = price_for(model)
        return round(tok.get("input", 0) * p.input_per_m / 1e6
                     + tok.get("output", 0) * p.output_per_m / 1e6, 6)
    except Exception:
        return None


def build(events: Iterable[Mapping]) -> dict:
    calls: dict[str, dict] = {}
    gates: dict[int, dict] = {}
    order: list[str] = []
    for e in events:
        t, sid, d = e.get("type"), e.get("step_id") or "", e.get("data") or {}
        if t == "step.started" and d.get("kind") == "agent.call":
            calls[sid] = {"step": sid, "shown": d.get("inputs"), "started": e.get("ts")}
            order.append(sid)
        elif t == "decision" and sid in calls:
            c = d.get("choice") or {}
            tok = d.get("tokens") or {}
            what = ("stop" if d.get("stop") else f"cap {c.get('power_cap_w')} W" if c.get("kind") == "cap"
                    else f"{c.get('knob')}={c.get('value')}")
            calls[sid].update(model=d.get("model"), choice=what, cap_w=c.get("power_cap_w"),
                              reasoning=d.get("reason"), tokens=tok,
                              usd={"value": _usd(d.get("model") or "", tok), "label": "ESTIMATE"},
                              fallback=d.get("model") == "fallback", label=d.get("label", "MEASURED"))
        elif t == "step.finished" and sid in calls:
            calls[sid]["latency_s"] = {"value": d.get("duration_s"), "label": "MEASURED"}
        elif t == "gate.result" and d.get("trial_no"):
            g = gates.setdefault(int(d["trial_no"]), {"passed": True, "checks": []})
            det = d.get("detail") or {}
            g["checks"].append({"check": d.get("check"), "passed": d.get("passed"),
                                "score": det.get("score"), "threshold": det.get("threshold")})
            g["passed"] = g["passed"] and bool(d.get("passed"))
    rows = []
    for i, sid in enumerate(order, 1):
        c = calls[sid]
        g = gates.get(c.get("cap_w") or -1)
        rows.append({"n": i, **c, "gate": g})
    kept = [r for r in rows if r.get("gate") and r["gate"]["passed"]]
    final = min((r["cap_w"] for r in kept), default=None)
    return {"spec": "wattnot.spark-trace/1", "calls": rows, "n_calls": len(rows),
            "found_cap_w": final,
            "total_latency_s": round(sum((r.get("latency_s") or {}).get("value") or 0 for r in rows), 2),
            "total_usd": {"value": round(sum((r.get("usd") or {}).get("value") or 0 for r in rows), 6),
                          "label": "ESTIMATE"},
            "label": "MEASURED"}


def from_file(path: str | Path) -> dict:
    ev = [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]
    out = build(ev)
    out["source"] = str(path)
    return out
