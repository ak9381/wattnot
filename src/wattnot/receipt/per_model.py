"""Per-model joules lines and the Spark trace id for a receipt.

A receipt may burn energy across several models: the GPU model under test (MEASURED from NVML),
and hosted helpers — the quality judge, the gate-explanation model and the Muse Spark planner.
Hosted providers report tokens, not joules, so a hosted line is either an ESTIMATE (tokens x a
published joules-per-token figure) or "not metered" when no token count is available. The served
model's energy is always MEASURED.

``spark_trace_id`` is a short, stable id for the planner call(s) that drove the decision, derived
from the planner events (or the receipt's planner_trace when events are absent). It lets a reader
pull the exact Spark timeline out of the run's event log.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

# Published joules-per-token for a hosted frontier model. Only ever used to label an ESTIMATE.
J_PER_TOKEN = 7.86


def _served(receipt: Mapping) -> dict:
    cand = receipt["candidate"]
    n = (receipt.get("subject", {}).get("workload") or {}).get("n_requests")
    jpa = round(float(cand["wh_per_req"]["mean"]) * 3600, 4)
    return {"model": f"{receipt['subject']['model']} (served, model under test)",
            "role": "served", "where": "local", "j_per_answer": jpa,
            "j_total": None if not n else round(jpa * n, 1),
            "label": receipt["measurement"]["label"],
            "how": "NVML energy counter, 10 samples a second; j_total = J per answer x checked answers"}


def _hosted(role: str, model: str, tokens: int, label: str = "ESTIMATE") -> dict:
    if not tokens:
        return {"model": model, "role": role, "where": "hosted", "j_per_answer": None,
                "j_total": None, "status": "not metered",
                "how": "hosted; the provider reports tokens, not joules"}
    return {"model": model, "role": role, "where": "hosted", "tokens": int(tokens),
            "j_per_answer": None, "j_total": round(tokens * J_PER_TOKEN, 1), "label": label,
            "how": f"ESTIMATE: {int(tokens)} tokens x {J_PER_TOKEN} J/token (provider gives tokens)"}


def _planner(planner_trace: Sequence[Mapping]) -> dict | None:
    spark = [t for t in planner_trace if t.get("source") == "spark"]
    if not spark:
        return None
    model = spark[0].get("model") or "muse-spark-1.3"
    tok = 0
    for t in spark:
        d = t.get("tokens") or {}
        tok += int(d.get("input", 0)) + int(d.get("output", 0))
    return _hosted("planner", f"{model} (planner, {len(spark)} call(s))", tok)


def per_model_joules(receipt: Mapping, *, roles: Sequence[Mapping] = ()) -> list[dict]:
    """Served model (MEASURED) + planner + any extra roles (judge, gate). Hosted => ESTIMATE."""

    lines = [_served(receipt)]
    planner = _planner(receipt.get("planner_trace") or [])
    if planner:
        lines.append(planner)
    for r in roles:
        if r.get("where", "hosted") == "local" and r.get("joules") is not None:
            lines.append({"model": r["model"], "role": r.get("role", "helper"), "where": "local",
                          "j_per_answer": r.get("j_per_answer"), "j_total": round(float(r["joules"]), 1),
                          "label": r.get("label", "MEASURED"),
                          "how": r.get("how", "metered on our own GPU")})
        else:
            lines.append(_hosted(r.get("role", "helper"), r["model"], int(r.get("tokens", 0)),
                                 r.get("label", "ESTIMATE")))
    return lines


def _digest(items: list) -> str | None:
    if not items:
        return None
    blob = json.dumps(items, separators=(",", ":"), sort_keys=True).encode()
    return "spark-" + hashlib.sha256(blob).hexdigest()[:16]


def spark_trace_id(*, events: Sequence[Mapping] = (), planner_trace: Sequence[Mapping] = ()) -> str | None:
    decisions = [e for e in events if e.get("type") == "decision"]
    if decisions:
        return _digest(decisions)
    spark = [t for t in planner_trace if t.get("source") == "spark"]
    return _digest(spark)


def attach(receipt: dict, *, events: Sequence[Mapping] = (), roles: Sequence[Mapping] = ()) -> dict:
    """Fill measurement.per_model_joules and measurement.spark_trace_id if not already set."""

    m = receipt["measurement"]
    if "per_model_joules" not in m:
        m["per_model_joules"] = per_model_joules(receipt, roles=roles)
    if "spark_trace_id" not in m:
        tid = spark_trace_id(events=events, planner_trace=receipt.get("planner_trace") or [])
        if tid:
            m["spark_trace_id"] = tid
    return receipt
