"""The adaptive top-k mass choice: the pure picking rule, and the saved CPU sweep it came from."""

import json
from pathlib import Path

from wattnot.moe import adaptive_topk as at
from wattnot.moe.adaptive_tune import (
    estimate_saving_pct,
    expert_cut_pct,
    pick_mass,
    summary_rows,
)

TUNE = Path(__file__).resolve().parent.parent / "data" / "adaptive-tune" / "tune.json"


def _sets(rows):  # {set: {mass: (agree, ci_lo, experts)}}
    return {n: {"by_mass": {f"{m:g}": {"agreement_pct": a, "ci95": [lo, 99.0], "positions": 100,
                                       "active_experts": e}
                            for m, (a, lo, e) in r.items()}} for n, r in rows.items()}


def test_pick_is_the_smallest_mass_that_clears_every_set():
    sets = _sets({"a": {0.9: (96.5, 95.5, 6.8), 0.95: (97.0, 96.0, 7.7)},
                  "b": {0.9: (93.0, 90.0, 6.8), 0.95: (96.2, 95.1, 7.7)}})
    assert pick_mass(sets)["mass"] == 0.95  # 0.9 fails on set b


def test_pick_needs_the_margin_and_the_lower_bound():
    only_ci = _sets({"a": {0.9: (95.5, 94.0, 7.0), 0.95: (96.0, 95.0, 7.7)}})
    assert pick_mass(only_ci)["mass"] == 0.95  # 95.5 is over the gate but under gate + margin
    assert pick_mass(_sets({"a": {0.9: (95.9, 99.0, 7.0)}}))["mass"] is None


def test_estimate_scales_the_one_measured_point():
    cut = expert_cut_pct(6.81127695686901, 8)
    assert round(cut, 1) == 14.9
    assert round(estimate_saving_pct(cut, cut, -6.6), 1) == -6.6
    assert round(estimate_saving_pct(cut / 2, cut, -6.6), 1) == -3.3
    assert estimate_saving_pct(5.0, 0.0, -6.6) == 0.0


def test_saved_cpu_sweep_gives_the_default_mass():
    d = json.loads(TUNE.read_text())
    pick = pick_mass(d["sets"])
    assert pick["mass"] == at.DEFAULT_MASS == 0.95
    for st in pick["per_set"].values():
        assert st["agreement_pct"] >= 96.0 and st["ci95_low"] >= 95.0
    rows = {r["mass"]: r for r in summary_rows(d["sets"], d["top_k"], d["run2_h100"])}
    assert all(r["label"] == "ESTIMATE" for r in rows.values())
    # the scaling reproduces the measured RUN2 point at mass 0.9 to within a few tenths
    assert abs(rows[0.9]["saving_estimate_pct"] - d["run2_h100"]["j_per_token_change_pct"]) < 0.5
    assert -2.5 < rows[0.95]["saving_estimate_pct"] < -1.0


def test_defaults_agree_everywhere():
    from wattnot.runbook import Plan

    assert Plan().adaptive_mass == at.DEFAULT_MASS
    assert at.AdaptiveConfig().mass == 0.95


def _fake_adaptive(tmp_path, capsys, *extra):
    from wattnot.proof import run as prun

    assert prun.main(["adaptive", "--fake", "--out", str(tmp_path), *extra]) == 0
    line = next(x for x in capsys.readouterr().out.splitlines() if x.startswith("ADAPTIVE_JSON "))
    return json.loads(line.split(" ", 1)[1])


def test_adaptive_command_uses_the_tuned_mass_and_the_flag_overrides_it(tmp_path, capsys):
    assert _fake_adaptive(tmp_path / "a", capsys)["mass"] == 0.95
    assert _fake_adaptive(tmp_path / "b", capsys, "--adaptive-mass", "0.96")["mass"] == 0.96


def test_agreement_prompts_are_longer_than_the_energy_set_and_start_the_same():
    cfg = at.AdaptiveConfig()
    energy, agree = at._prompts(cfg), at._agree_prompts(cfg)
    assert len(agree) == cfg.agree_prompts >= 32
    assert agree[:16] == energy[:16]  # RUN2's 16 prompts are still the first 16
    assert len(set(agree)) >= 32      # distinct prompts, not the same few repeated
