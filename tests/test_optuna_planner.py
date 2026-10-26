"""T23: Spark-warm-started Optuna and the cold/warm/grid ablation (recorded, no network)."""

from __future__ import annotations

import json
from pathlib import Path

from wattnot.planner.ablation import ablation, synthetic_objective
from wattnot.planner.optuna_study import (
    Bounds,
    PlannerContext,
    Proposal,
    make_study,
    propose,
    suggest,
)

REC = json.loads((Path(__file__).parent / "fixtures" / "spark_plan_recorded.json").read_text())
CTX = PlannerContext(Bounds(), 0.048, 1.0, 1.3)


def test_propose_from_recording_is_clamped_and_unique():
    props, usage = propose(CTX, 4, transport=lambda b: REC)
    assert 1 <= len(props) <= 4 and all(p.source == "spark" for p in props)
    b = Bounds()
    for p in props:
        assert b.cap_min_w <= p.params["power_cap_w"] <= b.cap_max_w
        assert p.params["max_num_seqs"] in b.max_num_seqs
        assert p.params["quantization"] in b.quantization
    assert len({tuple(p.params.values()) for p in props}) == len(props)
    assert usage["calls"] == 1 and usage["input"] > 0


def test_propose_sends_numbers_only():
    sent = []
    propose(CTX, 4, transport=lambda b: sent.append(b) or REC)
    user = json.loads(sent[0]["messages"][1]["content"])
    assert set(user) == {"n", "cap_w", "max_num_seqs", "quantization", "baseline",
                         "p99_budget_s"}


def test_invalid_json_falls_back():
    props, usage = propose(CTX, 4, transport=lambda b: {"choices": [{"message": {
        "content": "not json"}}]})
    assert len(props) == 4 and all(p.source == "fallback" for p in props)
    assert usage.get("fallback")


def test_clamp_snaps_to_bounds():
    assert Bounds().clamp({"power_cap_w": 9999, "max_num_seqs": 300, "quantization": "x"}) == \
        {"power_cap_w": 700, "max_num_seqs": 256, "quantization": "none"}
    assert Bounds().clamp({"power_cap_w": 512})["power_cap_w"] == 500


def test_enqueued_trials_run_first():
    a = {"power_cap_w": 550, "max_num_seqs": 512, "quantization": "fp8"}
    b = {"power_cap_w": 450, "max_num_seqs": 128, "quantization": "none"}
    seeds = [Proposal(a, "", "spark"), Proposal(b, "", "spark")]
    study = make_study(seeds, Bounds())
    seen = []
    study.optimize(lambda t: seen.append(suggest(t, Bounds())) or 0.0, n_trials=3)
    assert seen[:2] == [s.params for s in seeds]


def test_ablation_math_on_synthetic_objective():
    props, _ = propose(CTX, 4, transport=lambda b: REC)
    rep = ablation(20, proposals=props, spark={"calls": 1}).to_dict()
    assert rep["label"] == "ESTIMATE"
    assert [a["arm"] for a in rep["arms"]] == ["cold", "warm", "grid"]
    for a in rep["arms"]:
        assert a["n_trials"] == 20
        if a["trials_to_best"] is not None:
            assert a["seconds_to_best"] > 0 and a["wh_to_best"] > 0
            assert a["best_wh_per_req"] <= rep["target_wh_per_req"] * 1.01
    warm = rep["arms"][1]
    assert warm["trials_to_best"] is not None


def test_objective_feasibility_and_determinism():
    f = synthetic_objective()
    p = {"power_cap_w": 400, "max_num_seqs": 512, "quantization": "none"}
    assert f(p) == f(p) and f(p).p99_s > 1.3  # too slow: infeasible
    a = ablation(8, ("grid",)).to_dict()
    b = ablation(8, ("grid",)).to_dict()
    assert a == b
