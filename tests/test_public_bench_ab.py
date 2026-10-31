"""Public-benchmark A/B: pinned licensed slices, final-number GSM8K grader, sandboxed HumanEval
tests, alternating order and paired stats (no GPU needed)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "pb", Path(__file__).parents[1] / "scripts" / "public_bench_ab.py")
PB = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PB)


def test_slices_are_pinned_and_licensed():
    assert len(PB.load("gsm8k")) == 60 and len(PB.load("humaneval")) == 40
    for n, src in PB.SOURCES.items():
        assert src["licence"] == "MIT" and len(src["commit"]) == 40
        lic = (PB.DATA / f"LICENSE.{n}").read_text()
        assert "MIT" in lic and "Permission is hereby granted" in lic


def test_gsm8k_final_number_grader():
    item = PB.load("gsm8k")[0]
    gold = PB.gsm8k_gold(item)
    assert PB.gsm8k_ok(item, f"steps... 3 + 4 = 7\n#### {gold:g}")
    assert PB.gsm8k_ok(item, f"so the answer is {gold:,.0f}.")
    assert not PB.gsm8k_ok(item, f"#### {gold + 1:g}")
    assert not PB.gsm8k_ok(item, "")


def test_humaneval_runs_real_tests_in_sandbox():
    item = PB.load("humaneval")[0]   # has_close_elements
    good = ("```python\ndef has_close_elements(numbers, threshold):\n"
            "    return any(abs(a - b) < threshold for i, a in enumerate(numbers)"
            " for j, b in enumerate(numbers) if i != j)\n```")
    assert PB.he_ok(item, good)
    assert not PB.he_ok(item, "```python\ndef has_close_elements(numbers, threshold):\n    return True\n```")
    assert not PB.he_ok(item, "```python\ndef has_close_elements(n, t):\n    while True: pass\n```",
                        timeout=3)


def test_order_alternates_and_counterbalances():
    assert PB.order(3) == [("stock", 0), ("cap", 0), ("cap", 1), ("stock", 1),
                           ("stock", 2), ("cap", 2)]


def _r(arm, rep, j, c, n=10):
    return {"arm": arm, "rep": rep, "joules": j, "n": n, "correct": c, "errors": 0,
            "truncated": 0, "p50_s": 1.0, "p99_s": 2.0}


def test_summary_is_paired_j_per_correct():
    s = PB.summarize("gsm8k", [_r("stock", 0, 1000, 10), _r("cap", 0, 800, 10),
                               _r("stock", 1, 1000, 10), _r("cap", 1, 760, 10)])
    c = s["j_per_correct_change_pct"]
    assert c["n"] == 2 and round(c["mean"], 1) == -22.0 and c["p_value"] is not None
    assert s["stock"]["pass_pct"] == 100.0 and s["label"] == "MEASURED"


def test_one_round_gives_no_interval_not_nan():
    s = PB.summarize("gsm8k", [_r("stock", 0, 1000, 10), _r("cap", 0, 800, 10)])
    assert s["j_per_correct_change_pct"]["ci95_half"] is None
