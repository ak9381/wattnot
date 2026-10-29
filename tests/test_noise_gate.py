"""T12: Welch one-sided t-test, bootstrap CI noise gate, rollback on a noise fail."""

from __future__ import annotations

import math

import pytest

from wattnot.gates.noise import _saving_pct, bootstrap_saving_ci, noise_gate, welch

# Reference values from scipy.stats.ttest_ind(a, b, equal_var=False, alternative="greater")
REF = [
    ([0.0480, 0.0478, 0.0483, 0.0481, 0.0479], [0.0432, 0.0430, 0.0435, 0.0431, 0.0433],
     39.45575695328579, 8.0, 9.360321651230876e-11),
    ([1.0, 1.2, 0.9, 1.1, 1.05], [1.02, 1.15, 0.95, 1.08, 1.01],
     0.1323444445294575, 7.044165727125627, 0.44920647582901985),
    ([10, 12, 11, 13, 9, 10.5], [9, 9.5, 11, 8, 10],
     1.8439088914585764, 8.98408356130316, 0.04917870485844831),
]


@pytest.mark.parametrize("a,b,t,df,p", REF)
def test_welch_matches_scipy(a, b, t, df, p):
    w = welch(a, b)
    assert w.t == pytest.approx(t, rel=1e-9)
    assert w.df == pytest.approx(df, rel=1e-9)
    assert w.p_value == pytest.approx(p, rel=1e-6, abs=1e-12)


def test_bootstrap_is_stable_with_a_fixed_seed():
    a, b = REF[0][0], REF[0][1]
    assert bootstrap_saving_ci(a, b, n_boot=2000, seed=7) == \
        bootstrap_saving_ci(a, b, n_boot=2000, seed=7)
    lo, hi = bootstrap_saving_ci(a, b, n_boot=2000, seed=7)
    assert 0 < lo < 10.0 < hi


def test_identical_samples_are_noise():
    a = [0.048, 0.049, 0.047, 0.048, 0.050]
    r = noise_gate(a, list(a))
    assert not r.passed and r.gate == "noise" and r.detail["grade"] == "NOISE"
    assert r.detail["billed_saving_pct"] == 0


def test_clear_ten_percent_drop_with_one_percent_sd_is_billable():
    base = [1.00, 1.01, 0.99, 1.00, 1.01, 0.99, 1.00]
    cand = [0.90, 0.91, 0.89, 0.90, 0.91, 0.89, 0.90]
    r = noise_gate(base, cand)
    assert r.passed and r.detail["grade"] == "billable"
    d = r.detail
    assert d["p_value"] < 0.05 and 0 < d["ci_low"] <= d["saving_pct"] <= d["ci_high"]
    assert d["billed_saving_pct"] == d["ci_low"]
    assert {"t", "df", "p_value", "ci_low", "ci_high"} <= set(d)
    assert all(math.isfinite(d[k]) for k in ("t", "df", "p_value"))


def test_all_zero_arms_raise_a_clear_error_not_divide_by_zero():
    # mean(base) == 0 would divide by zero in the saving-percent step.
    with pytest.raises(ValueError, match="baseline mean is zero"):
        noise_gate([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])


def test_empty_arms_raise_value_error():
    with pytest.raises(ValueError):
        _saving_pct([], [1.0])
    with pytest.raises(ValueError):
        _saving_pct([1.0], [])


def test_failed_noise_gate_emits_rollback(tmp_path):
    from wattnot.events.emitter import Emitter
    from wattnot.gates import Decision, guarded_apply
    from wattnot.gates.noise import noise_gate as ng

    r = ng([1.0, 1.01, 0.99], [1.0, 1.01, 0.99])
    calls = []
    run = "run_20261028_100000_a1b2c3"
    with Emitter(run, root=tmp_path) as em:
        em.emit("gate.result", r.event_data("t1"), step_id="gate:t1:noise")
        applied = guarded_apply(Decision("t1", r.passed, (r,)), apply=lambda: calls.append("a"),
                                revert=lambda: calls.append("r"), change={"power_cap_w": 550},
                                emit=em)
    assert not applied and calls == ["r"]
    text = (tmp_path / "runs" / run / "events.jsonl").read_text()
    assert '"action.rolled_back"' in text and '"passed":false' in text.replace(' ', '')
