"""Small model first, big model only when the answer gate fails.

Every prompt is answered by Llama 3.1 8B on our own GPU first. A reference-free gate looks
at that answer right away (no ground truth needed at serving time):

* the reply was not cut off by the token limit,
* math has an explicit final answer (``#### n``, ``\\boxed{n}``, "the answer is n"),
* code has a Python block that parses,
* short QA is not empty and not a refusal.

Only when the gate fails does the prompt go to Muse Spark Open (Baseten or Modal). The
receipt then reports what matters to a buyer: joules and dollars per *correct* answer, for
three policies on the same prompts: small only, big only, and small-first.

Labels are never upgraded. Our GPU's joules are MEASURED. Hosted joules come from tokens
times our measured J/token, so they are an ESTIMATE, and any total that uses them is too.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping

from wattnot.gates.answers import _MARKERS  # same final-answer markers as the scoring gate

SMALL = "meta-llama/Llama-3.1-8B-Instruct"
BIG = "meta-models/Muse-Spark-Open-816B-A42B"
RANK = {"MEASURED": 0, "ESTIMATE": 1, "SIMULATED": 2, "MOCK": 3, "REPLAY": 0}
REFUSAL = re.compile(r"^\s*(i\s+(can(no|')t|am unable)|sorry)", re.IGNORECASE)
CODE_BLOCK = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)


def gate(kind: str, text: str | None, finish_reason: str | None = None) -> tuple[bool, str]:
    """Reference-free check of one small-model answer. (passed, reason)."""

    if not text or not text.strip():
        return False, "empty answer"
    if finish_reason == "length":
        return False, "cut off at the token limit"
    if kind == "math":
        if any(m.search(text) for m in _MARKERS):
            return True, "has a final answer"
        return False, "no final answer marker"
    if kind == "code":
        m = CODE_BLOCK.search(text)
        src = m.group(1) if m else (text if "def " in text else None)
        if src is None:
            return False, "no code block"
        try:
            ast.parse(src)
        except SyntaxError:
            return False, "code does not parse"
        return True, "code parses"
    if REFUSAL.match(text):
        return False, "refusal"
    return True, "non-empty answer"


def weakest(labels: Iterable[str]) -> str:
    labels = [x for x in labels if x]
    return max(labels, key=lambda x: RANK.get(x, 9)) if labels else "MOCK"


def _per(total: float, correct: int) -> float | None:
    return round(total / correct, 6) if correct else None


def _policy(name: str, rows: list[dict], label: str) -> dict:
    n = len(rows)
    correct = sum(r["correct"] for r in rows)
    j = sum(r["joules"] for r in rows)
    usd = sum(r["usd"] for r in rows)
    return {"policy": name, "n": n, "correct": correct,
            "accuracy_pct": round(100 * correct / n, 2) if n else None,
            "joules": round(j, 3), "usd": round(usd, 6),
            "j_per_correct": _per(j, correct), "usd_per_correct": _per(usd, correct),
            "escalated": sum(r.get("escalated", False) for r in rows), "label": label}


def evaluate(items: Iterable[Mapping]) -> dict:
    """items: {id, kind, small: {...}, big: {...}}; each side has text, finish_reason,
    correct, joules, usd, label. ``small.gate_pass`` may be given; else the gate is run.
    Returns the ``escalation`` receipt block."""

    small_rows, big_rows, casc = [], [], []
    labels_s, labels_b = [], []
    reasons: dict[str, int] = {}
    for it in items:
        s, b = it["small"], it.get("big") or {}
        labels_s.append(s.get("label", "MOCK"))
        if "gate_pass" in s:
            ok, why = bool(s["gate_pass"]), s.get("gate_reason", "given")
        else:
            ok, why = gate(it.get("kind", "qa"), s.get("text"), s.get("finish_reason"))
        small_rows.append({"correct": bool(s["correct"]), "joules": s["joules"], "usd": s["usd"]})
        if b:
            labels_b.append(b.get("label", "MOCK"))
            big_rows.append({"correct": bool(b["correct"]), "joules": b["joules"], "usd": b["usd"]})
        if ok or not b:
            casc.append({**small_rows[-1], "escalated": False})
        else:
            reasons[why] = reasons.get(why, 0) + 1
            casc.append({"correct": bool(b["correct"]), "joules": s["joules"] + b["joules"],
                         "usd": s["usd"] + b["usd"], "escalated": True})
    ls, lb = weakest(labels_s), weakest(labels_b) if labels_b else None
    out = {"small_model": SMALL, "big_model": BIG,
           "gate": "reference-free: not cut off, math has a final answer, code parses, no refusal",
           "policies": [_policy("small_only", small_rows, ls)]}
    if big_rows and len(big_rows) == len(small_rows):
        out["policies"].append(_policy("big_only", big_rows, lb))
        out["policies"].append(_policy("small_first", casc, weakest([ls, lb])))
    out["escalation_reasons"] = reasons
    sf = next((p for p in out["policies"] if p["policy"] == "small_first"), None)
    bo = next((p for p in out["policies"] if p["policy"] == "big_only"), None)
    if sf and bo and sf["j_per_correct"] and bo["j_per_correct"]:
        out["headline"] = {
            "j_per_correct_change_pct": round(100 * (sf["j_per_correct"] / bo["j_per_correct"] - 1), 1),
            "usd_per_correct_change_pct": round(100 * (sf["usd_per_correct"] / bo["usd_per_correct"] - 1), 1)
            if bo["usd_per_correct"] else None,
            "accuracy_change_pts": round(sf["accuracy_pct"] - bo["accuracy_pct"], 2),
            "versus": "big_only", "label": sf["label"]}
    return out


def router_row(block: Mapping) -> dict | None:
    """The small-first policy as one placement row for the routing table."""

    sf = next((p for p in block.get("policies", []) if p["policy"] == "small_first"), None)
    if not sf:
        return None
    return {"id": "small_first", "status": "ran",
            "pass_pct": {"value": sf["accuracy_pct"], "label": sf["label"]},
            "j_per_correct": {"value": sf["j_per_correct"], "label": sf["label"]},
            "usd_per_correct": {"value": sf["usd_per_correct"], "label": sf["label"]},
            "escalated_pct": round(100 * sf["escalated"] / sf["n"], 1) if sf["n"] else None}


def attach(receipt: dict, items: Iterable[Mapping]) -> dict:
    receipt["escalation"] = evaluate(items)
    return receipt
