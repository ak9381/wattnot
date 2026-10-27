"""The answer-checkable quality gate: it must pass good answers in any normal format and
must flag answers that are wrong, cut short or broken."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from wattnot.gates.answers import check_code, check_math, check_qa, extract_math_answer
from wattnot.gates.fixed import fixed_checks, pass_rate
from wattnot.runner.workload import load_workload

ROOT = Path(__file__).resolve().parents[1]
WL = load_workload(ROOT / "workloads" / "proof-v1.jsonl", kind="proof")
GOLD = {r["id"]: r["output"] for r in map(json.loads, (ROOT / "workloads" / "proof-v1.gold.jsonl")
                                           .read_text().splitlines())}


def test_workload_shape():
    assert len(WL) >= 100
    kinds = {i.meta["kind"] for i in WL}
    assert kinds == {"math", "code", "qa"}
    assert set(GOLD) == {i.id for i in WL}
    assert all(i.meta["checks"] for i in WL)


def test_gold_passes_everything():
    rate, n = pass_rate(GOLD, WL)
    assert n == len(WL) and rate == 100.0


@pytest.mark.parametrize("text", [
    "So the total is $1,200.00.\n#### 1200",
    "The final answer is \\boxed{1200}.",
    "Answer: 1,200",
    "The answer is 1200.0 dollars",
    "She ends up with 1200 dollars in total",   # no marker: last number
    "#### $1200",
    "answer is 2400/2",
])
def test_math_formats(text):
    assert check_math(text, "1200")


@pytest.mark.parametrize("text", [
    "#### 1201", "The answer is 120", "no number here", "", "I think it is 12000",
])
def test_math_wrong(text):
    assert not check_math(text, "1200")


def test_math_marker_beats_trailing_number():
    assert extract_math_answer("#### 18\nHope that helps with your 3 questions") is not None
    assert check_math("#### 18\nHope that helps with your 3 questions", "18")


def test_math_truncated_has_no_fallback():
    assert extract_math_answer("First 5 + 7 = 12 then", truncated=True) is None
    assert extract_math_answer("First 5 + 7 = 12", truncated=False) == 12


@pytest.mark.parametrize("text,ok", [
    ("Paris", True), ("paris.", True), ("The capital is Paris.", True),
    ("It is Lyon", False), ("", False),
    ("Brasília", True), ("brasilia", True),
    ("The answer to your long question about rivers, in some detail, is Paris and so on", False),
])
def test_qa(text, ok):
    acc = ["Paris", "Brasília", "Brasilia"] if "ras" in text.lower() else ["Paris"]
    assert check_qa(text, acc) is ok


GOOD = "Here you go:\n```python\ndef add(a, b):\n    return a + b\n```"


def test_code_pass_and_fail():
    tests = ["assert add(2, 3) == 5", "assert add(-1, 1) == 0"]
    assert check_code(GOOD, tests)
    assert not check_code(GOOD.replace("a + b", "a - b"), tests)
    assert not check_code("no code at all", tests)
    assert not check_code("```python\ndef add(a, b:\n```", tests)  # syntax error


def test_code_timeout_and_no_escape():
    assert not check_code("```python\ndef add(a, b):\n    while True:\n        pass\n```",
                          ["assert add(1, 1) == 2"], timeout_s=1.0)
    # the child runs in an empty temp dir with isolated mode
    src = "```python\nimport os\ndef add(a, b):\n    return os.listdir('/nonexistent')\n```"
    assert not check_code(src, ["assert add(1, 1) == 2"])


def test_code_plain_block_and_bare_text():
    assert check_code("```\ndef add(a, b):\n    return a + b\n```", ["assert add(1, 2) == 3"])
    assert check_code("def add(a, b):\n    return a + b", ["assert add(1, 2) == 3"])


# --- the gate as a whole: does it catch a worse setting? ---

def _truncate(text: str, frac: float) -> str:
    return text[: max(1, int(len(text) * frac))]


def test_gate_accepts_same_outputs():
    res = fixed_checks(GOLD, dict(GOLD), WL)
    assert res.passed


@pytest.mark.parametrize("frac", [0.5, 0.25])
def test_gate_flags_truncated_generation(frac):
    cut = {k: _truncate(v, frac) for k, v in GOLD.items()}
    res = fixed_checks(GOLD, cut, WL)
    assert not res.passed
    assert res.detail["candidate_pct"] < 60


def test_gate_flags_length_finish():
    flags = {k: True for k in list(GOLD)[:20]}
    res = fixed_checks(GOLD, dict(GOLD), WL, cand_truncated=flags)
    assert not res.passed


def test_gate_flags_corrupted_numbers_and_code():
    bad = {}
    for k, v in GOLD.items():
        if k.startswith("math"):
            v = re.sub(r"#### (\d+)", lambda m: f"#### {int(m.group(1)) + 1}", v)
        elif k.startswith("code"):
            v = v.replace("return", "return None and ", 1)
        else:
            v = "I am not sure."
        bad[k] = v
    res = fixed_checks(GOLD, bad, WL)
    assert not res.passed and res.detail["candidate_pct"] < 10


def test_gate_tolerance_is_a_number_of_items():
    keys = list(GOLD)
    one_bad = {**GOLD, keys[0]: "nonsense"}
    two_bad = {**one_bad, keys[1]: "nonsense"}
    assert fixed_checks(GOLD, one_bad, WL, max_drop_pts=1.0).passed       # 0.9 points
    assert not fixed_checks(GOLD, two_bad, WL, max_drop_pts=1.0).passed   # 1.8 points
    assert fixed_checks(GOLD, two_bad, WL, max_drop_pts=2.0).passed
