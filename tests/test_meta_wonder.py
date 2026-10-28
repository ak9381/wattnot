"""Tests for Muse Wonder supervisor: Pareto search, spend limits, and mock path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wattnot.meta.ledger import BudgetExceeded, SpendLedger, cost_usd
from wattnot.meta.wonder import (
    DEFAULT_CANDIDATES,
    WonderSupervisor,
    calculate_per_model_spend,
    normalize_trial,
    pareto_flattened,
    pareto_front,
    wonder_trace_id,
)


def test_normalize_trial_handles_alternate_keys():
    t1 = {
        "cap_w": 350,
        "clock_profile": "locked-mid",
        "joules_per_correct": 12.5,
        "quality": 0.95,
        "p99_delta": 4.2,
        "usd_per_1k": 0.005,
        "label": "MEASURED",
    }
    n1 = normalize_trial(t1)
    assert n1["cap_w"] == 350
    assert n1["clock_profile"] == "locked-mid"
    assert n1["joules_per_correct"] == 12.5

    t2 = {
        "cap_w": 300,
        "clock profile": "base",
        "joules per correct": 14.1,
        "quality": 0.92,
        "p99 delta": 5.0,
        "usd per 1k": 0.006,
        "label": "SIMULATED",
    }
    n2 = normalize_trial(t2)
    assert n2["clock_profile"] == "base"
    assert n2["joules_per_correct"] == 14.1
    assert n2["usd_per_1k"] == 0.006


def test_pareto_front_identifies_non_dominated_trials():
    trials = [
        {"cap_w": 400, "clock_profile": "base", "usd_per_1k": 0.010, "quality": 0.95, "joules_per_correct": 20.0},
        {"cap_w": 350, "clock_profile": "locked-high", "usd_per_1k": 0.008, "quality": 0.95, "joules_per_correct": 18.0},
        {"cap_w": 300, "clock_profile": "locked-mid", "usd_per_1k": 0.006, "quality": 0.94, "joules_per_correct": 15.0},
        {"cap_w": 250, "clock_profile": "locked-low", "usd_per_1k": 0.009, "quality": 0.90, "joules_per_correct": 17.0},  # Dominated by 350 & 300
    ]
    front = pareto_front(trials)
    caps = [t["cap_w"] for t in front]
    assert 250 not in caps
    assert 350 in caps and 300 in caps


def test_pareto_flattened_stops_when_improvement_below_threshold():
    # Progressively flattened trials
    base = [
        {"cap_w": 400, "clock_profile": "base", "usd_per_1k": 0.010, "quality": 0.90, "joules_per_correct": 20.0},
        {"cap_w": 350, "clock_profile": "locked-high", "usd_per_1k": 0.007, "quality": 0.90, "joules_per_correct": 16.0},
    ]
    # Small / flat trials in the last 3 steps
    tail = [
        {"cap_w": 320, "clock_profile": "locked-mid", "usd_per_1k": 0.00699, "quality": 0.90, "joules_per_correct": 15.9},
        {"cap_w": 310, "clock_profile": "locked-mid", "usd_per_1k": 0.00698, "quality": 0.90, "joules_per_correct": 15.9},
        {"cap_w": 300, "clock_profile": "locked-mid", "usd_per_1k": 0.00697, "quality": 0.90, "joules_per_correct": 15.8},
    ]
    history = base + tail

    flattened, impr, reason = pareto_flattened(history, threshold=0.01, patience=3)
    assert flattened is True
    assert impr < 0.01
    assert "Pareto front flattened" in reason


def test_budget_hard_cap_at_100_and_soft_warning_at_80(tmp_path):
    led = SpendLedger(tmp_path / "spend.json")
    assert led.cap_usd == 100.0
    assert led.soft_usd == 80.0

    # Seed spend to $79.90
    tokens_to_reach_79_90 = int(79.90 / 4.25 * 1e6)
    led.seed([{"key": "seed1", "model": "muse-spark-1.3", "purpose": "seed",
               "input_tokens": 0, "output_tokens": tokens_to_reach_79_90}])
    assert led.total() == pytest.approx(79.90, abs=0.02)

    # Next reserve with 100_000 max tokens ($0.425 worst-case) pushes total over 80.0
    body = {"model": "muse-spark-1.3", "max_tokens": 100_000, "messages": [{"role": "user", "content": "hi"}]}
    _, soft = led.reserve(body, "check-warning")
    assert soft is True

    # Fresh ledger to test exact hard cap at 100.0
    led2 = SpendLedger(tmp_path / "spend2.json")
    tokens_to_reach_99_90 = int(99.90 / 4.25 * 1e6)
    led2.seed([{"key": "seed2", "model": "muse-spark-1.3", "purpose": "seed2",
                "input_tokens": 0, "output_tokens": tokens_to_reach_99_90}])
    assert led2.total() == pytest.approx(99.90, abs=0.05)

    # A call that exceeds $100 hard cap is refused
    huge_call = {"model": "muse-spark-1.3", "max_tokens": 100_000, "messages": [{"role": "user", "content": "x" * 1000}]}
    with pytest.raises(BudgetExceeded, match="100.00 cap"):
        led2.reserve(huge_call, "overflow")


def test_wonder_supervisor_mock_path_and_exhaustion(tmp_path):
    candidates = [(400, "base"), (350, "locked-high")]
    sup = WonderSupervisor(candidates=candidates, mock=True)

    # Trial 1
    d1 = sup.next_trial([])
    assert d1.action == "trial"
    assert d1.cap_w == 400
    assert d1.clock_profile == "base"
    assert d1.trace_id.startswith("wonder-")

    # Trial 2
    history = [
        {"cap_w": 400, "clock_profile": "base", "usd_per_1k": 0.010, "quality": 0.95,
         "joules_per_correct": 20.0, "p99_delta": 0.0, "label": "MOCK"}
    ]
    d2 = sup.next_trial(history)
    assert d2.action == "trial"
    assert d2.cap_w == 350
    assert d2.clock_profile == "locked-high"

    # Candidates exhausted
    history.append({
        "cap_w": 350, "clock_profile": "locked-high", "usd_per_1k": 0.008, "quality": 0.94,
        "joules_per_correct": 17.0, "p99_delta": 5.0, "label": "MOCK"
    })
    d3 = sup.next_trial(history)
    assert d3.action == "stop"
    assert "exhausted" in d3.reason.lower()

    # Write summary
    json_p, md_p = sup.write_summary(history, tmp_path / "out", d3.reason)
    assert json_p.exists() and md_p.exists()

    summary_json = json.loads(json_p.read_text())
    assert summary_json["supervisor"] == "muse-wonder"
    assert summary_json["trials_count"] == 2
    assert "per_model_spend" in summary_json
    assert summary_json["per_model_spend"]["wonder"]["label"] in ("MOCK", "MEASURED")


def test_calculate_per_model_spend_tracks_all_five_roles(tmp_path):
    led = SpendLedger(tmp_path / "spend.json")
    led.seed([
        {"key": "1", "model": "muse-spark-1.3", "purpose": "spark-task", "input_tokens": 1000, "output_tokens": 1000},
        {"key": "2", "model": "muse-wonder", "purpose": "wonder-supervisor", "input_tokens": 2000, "output_tokens": 500},
    ])
    spend = calculate_per_model_spend(led)
    assert "spark" in spend
    assert "wonder" in spend
    assert "glimmer" in spend
    assert "llama" in spend
    assert "sam" in spend
    assert spend["spark"]["usd"] > 0
    assert spend["wonder"]["usd"] > 0
    assert spend["glimmer"]["usd"] == 0.0
