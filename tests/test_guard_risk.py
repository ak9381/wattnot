"""The risk classifier: small cap tweaks are low, everything else waits for a person."""
from __future__ import annotations

import json

import pytest

from wattnot.approvals import model as m
from wattnot.approvals.risk import RiskConfig, classify, load_config


def cap(before, after, **extra):
    change = {"target": "gpu:0", "gpu": 0, "before": {"power_cap_w": before},
              "after": {"power_cap_w": after}}
    change.update(extra)
    return {"kind": m.POWER_CAP, "change": change}


CFG = RiskConfig()


def test_small_drop_is_low():
    r = classify(cap(700, 650), CFG)
    assert r.level == "low" and not r.needs_approval
    assert r.inputs["drop_pct"] == pytest.approx(7.14, abs=0.01)


def test_exactly_15_percent_is_low():
    r = classify(cap(700, 595), CFG)  # exactly 15% below stock
    assert r.level == "low", r.reasons


def test_just_over_15_percent_is_high():
    r = classify(cap(700, 594), CFG)
    assert r.level == "high" and r.needs_approval
    assert any("more than 15% below stock" in x for x in r.reasons)


def test_exactly_70_percent_of_tdp_is_low():
    # a 30% drop needs a wider low_cap_drop_pct so only the TDP line is tested
    cfg = RiskConfig(low_cap_drop_pct=35)
    r = classify(cap(1000, 700), cfg, tdp_w=1000)  # exactly 70% of TDP
    assert r.level == "low", r.reasons


def test_just_under_70_percent_of_tdp_is_high():
    cfg = RiskConfig(low_cap_drop_pct=35)
    r = classify(cap(1000, 699), cfg, tdp_w=1000)
    assert r.level == "high"
    assert any("TDP" in x for x in r.reasons)


def test_raise_above_stock_is_low():
    assert classify(cap(500, 700), CFG).level == "low"


def test_many_gpus_needs_approval():
    assert classify(cap(700, 690, gpus=[0, 1]), CFG).level == "high"


def test_missing_numbers_are_high():
    r = classify({"kind": m.POWER_CAP, "change": {"after": {"power_cap_w": 500}}}, CFG)
    assert r.level == "high" and any("stock" in x for x in r.reasons)
    r = classify({"kind": m.POWER_CAP,
                  "change": {"before": {"power_cap_w": 700}, "after": {}}}, CFG)
    assert r.level == "high" and any("new power cap" in x for x in r.reasons)


def test_stock_arg_fills_a_missing_before():
    spec = {"kind": m.POWER_CAP, "change": {"gpu": 0, "after": {"power_cap_w": 650}}}
    assert classify(spec, CFG, stock_cap_w=700).level == "low"


def test_vllm_quantization_switch_is_high():
    base = {"kind": m.VLLM_CONFIG,
            "change": {"before": {"max_num_seqs": 32}, "after": {"max_num_seqs": 64}}}
    assert classify(base, CFG).level == "low"
    q = {"kind": m.VLLM_CONFIG,
         "change": {"before": {}, "after": {"quantization": "fp8"}}}
    assert classify(q, CFG).level == "high"
    back = {"kind": m.VLLM_CONFIG,
            "change": {"before": {"quantization": "fp8"}, "after": {}}}
    assert classify(back, CFG).level == "high"


def test_always_high_kinds():
    for kind, change in (
            (m.MOE_ROUTING, {"after": {"mass": 0.9}}),
            (m.FLEX_SHED, {"after": {"target_pct": 10}}),
            (m.TRIAL_GRANT, {"bounds": {"power_cap_w": {"min": 200, "max": 700}}})):
        r = classify({"kind": kind, "change": change}, CFG)
        assert r.level == "high" and r.reasons, kind


def test_cloud_policy_low_with_before():
    low = {"kind": m.CLOUD_POLICY,
           "change": {"before": {"effort": "high"}, "after": {"effort": "low"}}}
    assert classify(low, CFG).level == "low"
    high = {"kind": m.CLOUD_POLICY, "change": {"after": {"effort": "low"}}}
    assert classify(high, CFG).level == "high"


def test_unknown_kind_is_high():
    assert classify({"kind": "laser", "change": {"after": {}}}, CFG).level == "high"


@pytest.mark.parametrize("before,after,tdp,level", [
    (700, 700, None, "low"),    # no change
    (700, 600, None, "low"),    # ~14% drop
    (700, 595, None, "low"),    # exactly 15%
    (700, 500, None, "high"),   # ~29% drop
    (700, 400, 1000, "high"),   # below 70% of TDP
    (700, 650, 700, "low"),
])
def test_table(before, after, tdp, level):
    assert classify(cap(before, after), CFG, tdp_w=tdp).level == level


def test_config_defaults_when_file_missing(tmp_path):
    assert load_config(tmp_path) == RiskConfig()


def test_config_round_trip(tmp_path):
    (tmp_path / "guardrail.json").write_text(json.dumps(
        {"format": "wattnot.guardrail/1", "low_cap_drop_pct": 10,
         "high_below_tdp_pct": 80, "max_gpus_auto": 2, "approval_timeout_s": 120}))
    cfg = load_config(tmp_path)
    assert (cfg.low_cap_drop_pct, cfg.high_below_tdp_pct,
            cfg.max_gpus_auto, cfg.approval_timeout_s) == (10, 80, 2, 120)
    assert RiskConfig.from_dict(cfg.to_dict()) == cfg


@pytest.mark.parametrize("doc", [
    {"format": "wattnot.guardrail/9"},
    {"format": "wattnot.guardrail/1", "nope": 1},
    {"format": "wattnot.guardrail/1", "low_cap_drop_pct": 0},
    {"format": "wattnot.guardrail/1", "high_below_tdp_pct": 101},
    {"format": "wattnot.guardrail/1", "max_gpus_auto": 0},
    {"format": "wattnot.guardrail/1", "approval_timeout_s": -5},
    {"format": "wattnot.guardrail/1", "max_gpus_auto": "many"},
])
def test_config_rejects_junk(tmp_path, doc):
    (tmp_path / "guardrail.json").write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        load_config(tmp_path)
