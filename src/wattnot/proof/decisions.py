"""Every planner decision as one JSON line in the run's output directory.

Each proposal (Muse Spark or the bisect fallback) is recorded in
``decisions.jsonl``: the step, who picked, the allowed caps, the cap proposed,
the one-line reason, whether validation accepted the Muse answer, whether the
bisect rule picked instead and why, the call's token usage and USD, and later
the gate outcome with its measured numbers. The file is append-only: one line
is added per proposal and lines are never rewritten. Each line validates
against ``schemas/decision.schema.json``.

``explain_decision`` builds a plain-words account from the record's real
numbers with no API call. Muse Spark's own ``explanation`` is stored beside
that text, never instead of it. Records hold numbers and short reasons only:
no prompts, no model outputs, no secrets.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from wattnot.schemas import validate_decision

DECISIONS_FILE = "decisions.jsonl"


def new_record(step: int, *, allowed: Sequence[int], proposal: Mapping) -> dict:
    """One record for a fresh proposal; the gate outcome is still pending."""

    by = str(proposal.get("by") or "bisect")
    fallback = by == "bisect-fallback"
    usage = proposal.get("usage")
    record = {
        "step": int(step),
        "planner": "muse-spark" if not fallback else "bisect",
        "allowed_caps": [int(w) for w in allowed],
        "proposed_cap_w": proposal.get("cap_w"),
        "reason": str(proposal.get("reason") or ""),
        "muse_explanation": proposal.get("explanation"),
        "explanation": "",
        "proposal_accepted": proposal.get("proposal_accepted"),
        "fallback": fallback,
        "fallback_reason": (str(proposal.get("muse_error") or "")[:200] or None)
        if fallback and proposal.get("muse_error")
        else None,
        "usage": dict(usage) if isinstance(usage, Mapping) else None,
        "usd": proposal.get("usd"),
        "gate": "pending" if proposal.get("kind") == "try" else "stop",
        "p99_change_pct": None,
        "energy_change_pct": None,
        "quality_pass_pct": None,
        "gate_why": [],
    }
    record["explanation"] = explain_decision(record)
    return record


def settle_record(record: dict, summary: Mapping | None) -> dict:
    """Fill in the gate outcome from a step summary (None for kind=stop)."""

    if summary is None:
        record["gate"] = "stop"
    else:
        record["gate"] = "accepted" if summary["accepted"] else "rejected"
        record["p99_change_pct"] = float(summary["p99_change_pct"]["mean"])
        record["energy_change_pct"] = float(summary["energy_change_pct"]["mean"])
        record["quality_pass_pct"] = float(summary["quality"]["cap_pct"])
        record["gate_why"] = [str(w) for w in summary.get("why_not", [])]
    record["explanation"] = explain_decision(record)
    return record


def _num(value: object, *, signed: bool = True) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "n/a"
    return f"{value:+.1f}%" if signed else f"{value:.1f}%"


def explain_decision(record: Mapping) -> str:
    """Plain-words account of one decision, 1-2 sentences, from the record alone."""

    planner = record.get("planner", "bisect")
    who = "Muse Spark" if planner == "muse-spark" else "The bisect rule"
    reason = str(record.get("reason") or "").strip()
    tail = f": {reason}." if reason else "."
    cap = record.get("proposed_cap_w")
    gate = record.get("gate", "pending")
    if gate == "stop" or cap is None:
        return f"{who} proposed stopping{tail} No more caps were tried."
    if record.get("fallback"):
        why = record.get("fallback_reason") or "its answer failed validation"
        lead = (f"Muse Spark's answer was rejected ({why}), so the bisect rule "
                f"picked {cap} W instead{tail}")
    else:
        lead = f"{who} proposed {cap} W{tail}"
    if gate == "pending":
        return lead + " The gate has not ruled yet."
    numbers = (f"p99 {_num(record.get('p99_change_pct'))}, energy "
               f"{_num(record.get('energy_change_pct'))} per request, quality "
               f"{_num(record.get('quality_pass_pct'), signed=False)} pass")
    if gate == "accepted":
        return lead + f" The gate ACCEPTED it (MEASURED): {numbers}."
    why = "; ".join(record.get("gate_why") or ["it broke a gate limit"])
    return lead + f" The gate REJECTED it (MEASURED): {why}; {numbers}."


def summarize_decisions(records: Sequence[Mapping]) -> dict:
    """Counts by planner, validation and gate acceptance rates, fallback count."""

    rows = list(records)
    by: dict[str, int] = {}
    for r in rows:
        by[str(r.get("planner", "?"))] = by.get(str(r.get("planner", "?")), 0) + 1
    checked = [r for r in rows if r.get("proposal_accepted") is not None]
    kept = [r for r in checked if r.get("proposal_accepted")]
    decided = [r for r in rows if r.get("gate") in ("accepted", "rejected")]
    good = [r for r in decided if r.get("gate") == "accepted"]
    fallbacks = sum(1 for r in rows if r.get("fallback"))
    return {
        "total": len(rows),
        "by_planner": by,
        "muse_checked": len(checked),
        "muse_accepted": len(kept),
        "validation_rate": (len(kept) / len(checked) if checked else None),
        "gate_decided": len(decided),
        "gate_accepted": len(good),
        "gate_acceptance_rate": (len(good) / len(decided) if decided else None),
        "fallbacks": fallbacks,
    }


def format_decisions_summary(summary: Mapping) -> str:
    """One or two plain lines for the run report."""

    parts = ", ".join(f"{k} {v}" for k, v in sorted(summary.get("by_planner", {}).items()))
    lead = (f"{summary.get('total', 0)} planner proposals ({parts}). "
            f"Gate acceptance {summary.get('gate_accepted', 0)}/"
            f"{summary.get('gate_decided', 0)} (MEASURED). "
            f"Fallbacks to bisect: {summary.get('fallbacks', 0)}.")
    if summary.get("muse_checked"):
        return lead + (f" Validation accepted {summary.get('muse_accepted', 0)}/"
                       f"{summary.get('muse_checked', 0)} Muse answers.")
    return lead


def append_record(out: Path, record: dict) -> dict:
    """Schema-check one record and append it as a single JSON line."""

    validate_decision(record)
    out.mkdir(parents=True, exist_ok=True)
    with (out / DECISIONS_FILE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def load_records(out: Path) -> list[dict]:
    """Read and schema-check every decision line, in file order."""

    path = out / DECISIONS_FILE
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    for row in rows:
        validate_decision(row)
    return rows
