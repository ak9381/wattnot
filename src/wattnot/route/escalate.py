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
from collections.abc import Iterable, Mapping, Sequence

from wattnot.gates.answers import _MARKERS  # same final-answer markers as the scoring gate

SMALL = "meta-llama/Llama-3.1-8B-Instruct"
MID = "meta-models/Muse-Glimmer-30B"
BIG = "meta-models/Muse-Spark-Open-816B-A42B"
# The all-Meta escalation chain, smallest first. Each prompt is tried at a tier; the
# reference-free gate decides keep-or-escalate. The last tier is terminal (always answers).
TIER_CHAIN = (
    {"name": "llama-8b", "model": SMALL, "where": "local"},
    {"name": "glimmer-30b", "model": MID, "where": "local"},
    {"name": "spark-open", "model": BIG, "where": "hosted"},
)
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


def evaluate(items: Iterable[Mapping], *, overhead_joules: float = 0.0,
             overhead_label: str = "MEASURED") -> dict:
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
    n = len(small_rows)
    out["small_model_pass_pct"] = round(100 * sum(r["correct"] for r in small_rows) / n, 2) if n else None
    out["gate_pass_pct"] = round(100 * sum(not c.get("escalated") for c in casc) / n, 2) if n else None
    out["escalation_rate_pct"] = round(100 * sum(c.get("escalated", False) for c in casc) / n, 2) if n else None
    # net: the gate, the judge and verification also burn energy; charge it to small-first
    out["overhead"] = {"joules": round(overhead_joules, 3), "label": overhead_label,
                       "what": "agent/planner, quality judge and verification energy, charged to small_first"}
    sf = next((p for p in out["policies"] if p["policy"] == "small_first"), None)
    if sf and overhead_joules:
        sf["joules_gross"] = sf["joules"]
        sf["joules"] = round(sf["joules"] + overhead_joules, 3)
        sf["j_per_correct"] = _per(sf["joules"], sf["correct"])
        sf["label"] = weakest([sf["label"], overhead_label])
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


# --- Three-tier (N-tier) chain -------------------------------------------------------------
#
# ``evaluate_chain`` generalises the two-tier small-first policy to an ordered list of tiers.
# Each item carries ``attempts``: one per tier, smallest first, each with text, finish_reason,
# correct, joules, usd, label (and optional gate_pass/gate_reason). The gate runs after each
# attempt; the first tier whose gate passes answers the prompt, otherwise we escalate. The last
# tier is terminal: it answers whether or not its gate passes. Energy and dollars are summed
# across every tier that actually ran (the loop stops at the answering tier).


def _attempt_gate(kind: str, att: Mapping) -> tuple[bool, str]:
    if "gate_pass" in att:
        return bool(att["gate_pass"]), att.get("gate_reason", "given")
    return gate(kind, att.get("text"), att.get("finish_reason"))


def evaluate_chain(items: Iterable[Mapping], *, tiers: Sequence[Mapping] = TIER_CHAIN,
                   overhead_joules: float = 0.0, overhead_label: str = "MEASURED") -> dict:
    """Run the escalation chain over ``items`` and return the ``escalation`` receipt block.

    Logs, per item, which tier answered and the gate pass/fail at every tier it reached.
    Aggregates the escalation rate (share of items that went past the first tier) and the
    small-model pass rate (share the first tier answered), plus a per-tier pass rate.
    """

    items = list(items)
    names = [t["name"] for t in tiers]
    attempted = {n: 0 for n in names}
    gate_passes = {n: 0 for n in names}
    answered = {n: 0 for n in names}
    per_item: list[dict] = []
    chain_rows: list[dict] = []
    tier0_rows: list[dict] = []
    labels_all: list[str] = []
    labels_tier0: list[str] = []
    reasons: dict[str, int] = {}

    for it in items:
        kind = it.get("kind", "qa")
        attempts = list(it["attempts"])
        if not attempts:
            raise ValueError("each item needs at least one tier attempt")
        j = usd = 0.0
        trail: list[dict] = []
        ans_idx = len(attempts) - 1
        for i, att in enumerate(attempts):
            name = names[i]
            attempted[name] += 1
            j += float(att["joules"])
            usd += float(att["usd"])
            lab = att.get("label", "MOCK")
            labels_all.append(lab)
            if i == 0:
                labels_tier0.append(lab)
                tier0_rows.append({"correct": bool(att["correct"]),
                                   "joules": float(att["joules"]), "usd": float(att["usd"])})
            ok, why = _attempt_gate(kind, att)
            if ok:
                gate_passes[name] += 1
            is_last = i == len(attempts) - 1
            trail.append({"tier": name, "model": tiers[i]["model"], "gate_pass": ok,
                          "gate_reason": why, "correct": bool(att["correct"]),
                          "joules": round(float(att["joules"]), 3),
                          "usd": round(float(att["usd"]), 6), "label": lab})
            if ok or is_last:
                ans_idx = i
                break
            reasons[why] = reasons.get(why, 0) + 1
        ans_name = names[ans_idx]
        answered[ans_name] += 1
        correct = bool(attempts[ans_idx]["correct"])
        chain_rows.append({"correct": correct, "joules": j, "usd": usd,
                           "escalated": ans_idx > 0})
        per_item.append({"id": it.get("id"), "kind": kind, "answered_by": ans_name,
                         "answered_tier": ans_idx, "escalated": ans_idx > 0,
                         "correct": correct, "trail": trail})

    n = len(items)
    lab_all = weakest(labels_all)
    lab0 = weakest(labels_tier0)
    policies = [_policy("tier0_only", tier0_rows, lab0),
                _policy("chain", chain_rows, lab_all)]
    chain_pol = policies[1]
    if overhead_joules:
        chain_pol["joules_gross"] = chain_pol["joules"]
        chain_pol["joules"] = round(chain_pol["joules"] + overhead_joules, 3)
        chain_pol["j_per_correct"] = _per(chain_pol["joules"], chain_pol["correct"])
        chain_pol["label"] = weakest([chain_pol["label"], overhead_label])
    tier_rows = [{"name": t["name"], "model": t["model"], "where": t.get("where", "local"),
                  "attempted": attempted[t["name"]], "gate_passes": gate_passes[t["name"]],
                  "answered": answered[t["name"]],
                  "pass_pct": round(100 * gate_passes[t["name"]] / attempted[t["name"]], 2)
                  if attempted[t["name"]] else None} for t in tiers]
    return {
        "spec": "wattnot.escalation-chain/1",
        "chain": [{"name": t["name"], "model": t["model"], "where": t.get("where", "local")}
                  for t in tiers],
        "gate": "reference-free: not cut off, math has a final answer, code parses, no refusal",
        "policies": policies,
        "tiers": tier_rows,
        "per_item": per_item,
        "escalation_reasons": reasons,
        "n": n,
        "small_model_pass_pct": round(100 * gate_passes[names[0]] / n, 2) if n else None,
        "escalation_rate_pct": round(100 * sum(r["escalated"] for r in chain_rows) / n, 2)
        if n else None,
        "overhead": {"joules": round(overhead_joules, 3), "label": overhead_label,
                     "what": "gate, quality judge and verification energy, charged to the chain"},
        "label": lab_all,
    }


def chain_leaderboard_row(block: Mapping, *, setup: str, hardware: str = "1x H100",
                          era: str = "RUN3") -> dict:
    """One leaderboard row (net J per correct answer) from an ``evaluate_chain`` block."""

    chain = next((p for p in block.get("policies", []) if p["policy"] == "chain"), None)
    models = " -> ".join(t["model"].split("/")[-1] for t in block.get("chain", []))
    jpc = chain.get("j_per_correct") if chain else None
    return {"setup": setup, "model": models, "hardware": hardware,
            "j_per_correct": None if jpc is None else {"mean": jpc, "sd": None},
            "n_checked": chain.get("n") if chain else None, "repeats": 1,
            "pass_pct": chain.get("accuracy_pct") if chain else None,
            "p99_change_pct": None,
            "escalation_rate_pct": block.get("escalation_rate_pct"),
            "small_model_pass_pct": block.get("small_model_pass_pct"),
            "gate": "escalation chain", "era": era,
            "label": chain.get("label") if chain else block.get("label"),
            "status": "measured" if (chain and chain.get("label") == "MEASURED") else "illustration",
            "note": "all-Meta chain; net of gate/judge/verification energy"}


def attach_chain(receipt: dict, items: Iterable[Mapping], **kw) -> dict:
    receipt["escalation"] = evaluate_chain(items, **kw)
    return receipt
