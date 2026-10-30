"""The vision quality gate: do the served model's answers match ground truth?

Each eye-test item has a ``kind`` that picks a forgiving matcher (the model
may answer in a short phrase, so we look for the known token, not an exact
string). A single miss fails the gate — the whole point is that one broken
image rejects the candidate. The gate returns the same ``GateResult`` shape
as the quality / p99 / noise gates, so ``decide`` can run it in the same loop.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from wattnot.gates.result import GateResult
from wattnot.vision.assets import EST_WH_PER_IMAGE, EyeTestItem

# A missing answer is capped so it can never bloat the receipt or leak text.
_MAX_ANSWER = 64
_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _nums(text: str) -> list[str]:
    return [m.group(0).replace(",", "") for m in _NUM.finditer(str(text or ""))]


def _matches(kind: str, expected: str, got: str) -> bool:
    exp, g = _norm(expected), _norm(got)
    if not g:
        return False
    if kind in ("number", "count"):
        want = _nums(exp)
        if not want:
            return False
        got_nums = _nums(g)
        try:
            return any(abs(float(n) - float(want[0])) < 1e-6 for n in got_nums)
        except ValueError:
            return want[0] in got_nums
    # text / color: case-insensitive whole-token match, tolerant of surrounding words.
    exp_u, g_u = exp.upper(), g.upper()
    if exp_u == g_u:
        return True
    return re.search(rf"(?<![A-Z0-9]){re.escape(exp_u)}(?![A-Z0-9])", g_u) is not None


def grade_answers(answers: Mapping[str, str], items: Sequence[EyeTestItem], *,
                  wh_per_image: float = EST_WH_PER_IMAGE) -> list[dict]:
    """Per-image results. ``got`` is the model's answer, trimmed; a missing
    answer counts as a miss. Each image carries an ESTIMATE energy stamp."""

    rows: list[dict] = []
    for it in items:
        raw = answers.get(it.id)
        got = _norm(raw)[:_MAX_ANSWER] if raw is not None else ""
        rows.append({
            "id": it.id,
            "kind": it.kind,
            "question": it.question,
            "expected": it.answer[:_MAX_ANSWER],
            "got": got,
            "ok": bool(raw is not None and _matches(it.kind, it.answer, got)),
            "label": it.label or "SYNTHETIC",
            "energy_wh": {"value": round(float(wh_per_image), 6), "label": "ESTIMATE"},
        })
    return rows


def vision_gate(answers: Mapping[str, str], items: Sequence[EyeTestItem], *,
                wh_per_image: float = EST_WH_PER_IMAGE) -> GateResult:
    """Pass only if every SYNTHETIC test image is answered correctly."""

    items = list(items)
    rows = grade_answers(answers, items, wh_per_image=wh_per_image)
    missed = [r["id"] for r in rows if not r["ok"]]
    checked = len(rows)
    passed = checked > 0 and not missed
    detail = {
        "checked": checked,
        "passed_count": checked - len(missed),
        "missed": missed,
        "images": rows,
        "label": "SYNTHETIC",
        "est_wh_per_image": round(float(wh_per_image), 6),
        "blocks_musecode_pr": bool(missed),
    }
    if checked == 0:
        return GateResult("vision", False, "no eye-test images to check", detail)
    if passed:
        return GateResult("vision", True, f"all {checked} eye-test images read correctly", detail)
    show = ", ".join(missed[:6]) + (" …" if len(missed) > 6 else "")
    return GateResult("vision", False,
                      f"vision misread {len(missed)}/{checked} image(s): {show}", detail)
