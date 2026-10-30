"""The spoken-answer quality gate: does the served model's answer to each
transcribed question match the known answer?

After a candidate serving change the served model (Glimmer) answers the 20
SYNTHETIC spoken questions (their text comes from the cached Muse Voice
Transcribe output). A single wrong spoken answer fails the gate — exactly like
the eye test — so a config that quietly breaks spoken comprehension is rejected
and rolled back. The gate returns the same ``GateResult`` shape as the quality
/ p99 / noise / vision gates, so ``decide`` can run it in the same loop.

Alongside the pass/fail, ``spoken_energy`` reports joules per correct spoken
answer (MEASURED on the H100) next to joules per token and joules per correct
answer, so the dashboard can show the three side by side.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from wattnot.gates.result import GateResult
from wattnot.spokenanswer.assets import SpokenItem

# A model answer is capped so it can never bloat the receipt or leak text.
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
    if kind == "number":
        want = _nums(exp)
        if not want:
            return False
        got_nums = _nums(g)
        try:
            return any(abs(float(n) - float(want[0])) < 1e-6 for n in got_nums)
        except ValueError:
            return want[0] in got_nums
    # text: case-insensitive whole-token match, tolerant of surrounding words.
    exp_u, g_u = exp.upper(), g.upper()
    if exp_u == g_u:
        return True
    return re.search(rf"(?<![A-Z0-9]){re.escape(exp_u)}(?![A-Z0-9])", g_u) is not None


def grade_answers(answers: Mapping[str, str], items: Sequence[SpokenItem]) -> list[dict]:
    """Per-question results. ``got`` is the model's answer, trimmed; a missing
    answer counts as a miss. Each row records whether the take was clean or
    recorded over data-center noise."""

    rows: list[dict] = []
    for it in items:
        raw = answers.get(it.id)
        got = _norm(raw)[:_MAX_ANSWER] if raw is not None else ""
        rows.append({
            "id": it.id,
            "kind": it.kind,
            "noise": it.noise,
            "question": it.question,
            "expected": it.answer[:_MAX_ANSWER],
            "got": got,
            "ok": bool(raw is not None and _matches(it.kind, it.answer, got)),
            "label": it.label or "SYNTHETIC",
        })
    return rows


def spoken_energy(*, wh_per_answer: float, tokens_per_answer: float, pass_rate: float,
                  label: str = "MEASURED",
                  j_per_correct_answer: float | None = None) -> dict[str, Any]:
    """Joules per correct spoken answer (and context metrics) from measured Wh.

        j_per_spoken_answer  = wh_per_answer x 3600
        j_per_correct_spoken = j_per_spoken_answer / spoken pass_rate
        j_per_token          = j_per_spoken_answer / tokens per answer

    ``j_per_correct_answer`` is the run's existing joules-per-correct-(text)-answer,
    carried through unchanged so the panel can show the three numbers together.
    The ``label`` is MEASURED on a metered H100 run; the ``--fake`` runner, which
    has no meter, passes ``MOCK``.
    """

    j = float(wh_per_answer) * 3600.0
    good = round(j / pass_rate, 6) if pass_rate and pass_rate > 0 else None
    per_tok = round(j / tokens_per_answer, 6) if tokens_per_answer and tokens_per_answer > 0 else None
    out: dict[str, Any] = {
        "label": label,
        "meter": "NVML internal energy counter via pynvml",
        "wh_per_answer": round(float(wh_per_answer), 6),
        "j_per_spoken_answer": round(j, 6),
        "j_per_correct_spoken": good,
        "j_per_token": per_tok,
    }
    if j_per_correct_answer is not None:
        out["j_per_correct_answer"] = round(float(j_per_correct_answer), 6)
    return out


def spoken_answer_gate(answers: Mapping[str, str], items: Sequence[SpokenItem], *,
                       energy: Mapping[str, Any] | None = None) -> GateResult:
    """Pass only if every SYNTHETIC spoken question is answered correctly."""

    items = list(items)
    rows = grade_answers(answers, items)
    missed = [r["id"] for r in rows if not r["ok"]]
    checked = len(rows)
    passed = checked > 0 and not missed
    noisy = [r["id"] for r in rows if r["noise"] == "datacenter" and not r["ok"]]
    detail: dict[str, Any] = {
        "checked": checked,
        "passed_count": checked - len(missed),
        "missed": missed,
        "missed_noisy": noisy,
        "items": rows,
        "label": "SYNTHETIC",
        "blocks_musecode_pr": bool(missed),
    }
    if energy is not None:
        detail["energy"] = dict(energy)
    if checked == 0:
        return GateResult("spoken", False, "no spoken questions to check", detail)
    if passed:
        return GateResult("spoken", True,
                          f"all {checked} spoken answers correct", detail)
    show = ", ".join(missed[:6]) + (" …" if len(missed) > 6 else "")
    return GateResult("spoken", False,
                      f"spoken misheard {len(missed)}/{checked} answer(s): {show}", detail)
