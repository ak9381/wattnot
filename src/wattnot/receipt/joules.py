"""Joules per answer: two derived numbers every receipt can carry.

    j_per_answer       = Wh per request x 3600
    j_per_good_answer  = j_per_answer / pass_rate

`pass_rate` is the share of answers that passed the quality gate
(`quality.fixed_pass_rate`, 0 to 1). A change that is cheap but fails the gate
more often therefore looks worse here, not better. With a pass rate of 0
there is no good answer, so the number is null.

The block is stored inside the receipt (and so inside the hash) and the verifier
re-derives it from the arms. A receipt whose `joules` block does not match its own
numbers is not trusted. Where the energy is a range (hosted models), the range is
carried through as `low` and `high`.

The label is the receipt's measurement label: MEASURED, ESTIMATE or SIMULATED.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

TOL = 1e-6  # absolute tolerance when comparing a stored block with a re-derivation
ARMS = ("baseline", "candidate")


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 6)


def _good(j: float, pass_rate: float | None) -> float | None:
    if not pass_rate or pass_rate <= 0:
        return None
    return j / pass_rate


def arm_joules(arm: Mapping) -> dict[str, Any]:
    """Derive the joules numbers for one arm (`baseline` or `candidate`)."""

    wh = arm["wh_per_req"]
    pass_rate = (arm.get("quality") or {}).get("fixed_pass_rate")
    j = float(wh["mean"]) * 3600
    out: dict[str, Any] = {"j_per_answer": _r(j), "pass_rate": pass_rate,
                           "j_per_good_answer": _r(_good(j, pass_rate))}
    rng = wh.get("range")
    if isinstance(rng, Mapping):
        lo, hi = float(rng["low"]) * 3600, float(rng["high"]) * 3600
        out["j_per_answer_range"] = {"low": _r(lo), "high": _r(hi)}
        glo, ghi = _good(lo, pass_rate), _good(hi, pass_rate)
        out["j_per_good_answer_range"] = {"low": _r(glo), "high": _r(ghi)}
    return out


def _change_pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or a == 0:
        return None
    return round(100 * (b - a) / a, 4)


def joules_block(receipt: Mapping) -> dict[str, Any]:
    """The `joules` block for a receipt body. Needs both arms."""

    base, cand = arm_joules(receipt["baseline"]), arm_joules(receipt["candidate"])
    return {
        "unit": "J",
        "label": receipt["measurement"]["label"],
        "formula": "j_per_answer = wh_per_req x 3600; j_per_good_answer = j_per_answer / pass_rate",
        "baseline": base,
        "candidate": cand,
        "change_pct": {
            "j_per_answer": _change_pct(base["j_per_answer"], cand["j_per_answer"]),
            "j_per_good_answer": _change_pct(base["j_per_good_answer"],
                                             cand["j_per_good_answer"]),
        },
    }


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(a - b) <= TOL
    return a == b


def check_joules(receipt: Mapping) -> tuple[bool, dict[str, Any]]:
    """Re-derive and compare. Returns (ok, detail). Absent block is fine (ok, absent)."""

    stored = receipt.get("joules")
    try:
        derived = joules_block(receipt)
    except (KeyError, TypeError, ValueError) as e:
        if stored is None:
            return True, {"present": False}
        return False, {"present": True, "error": f"cannot re-derive: {e}"[:200]}
    if stored is None:
        return True, {"present": False, "derived": derived}
    ok = _same(stored, derived)
    detail: dict[str, Any] = {"present": True, "derived": derived}
    if not ok:
        detail["error"] = "joules block does not match the receipt's own numbers"
    return ok, detail


def with_joules(receipt: dict) -> dict:
    """Add the block to a receipt body that has no `integrity` yet."""

    receipt["joules"] = joules_block(receipt)
    return receipt
