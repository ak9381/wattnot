"""A/B fairness review (review/ab-fairness-1031), item 7 fix:

the prefix-caching A/B and the combined block (best cap + prefix ON vs stock + prefix OFF) must
report a CI and a p-value on the PAIRED per-rep difference — the same rigour the power-cap sweep
uses (wattnot.proof.session.summarize) — not a bare mean-to-mean point estimate.
"""

from __future__ import annotations

from wattnot.context_saver import prefix_cache as PC


def _rows(jprs, pass_pct=96.0):
    return [{"j_per_req": j, "pass_pct": pass_pct, "ttft_ms": 100.0, "p99_ms": 3000.0,
             "cache_hit_rate": 0.8, "reasoning_measured": True} for j in jprs]


def test_paired_change_clear_saving_has_ci_below_zero_and_small_p():
    # off ~ 11 J/req, on ~ 7 J/req, three reps with a little noise -> a real saving.
    off = PC.paired_change([11.0, 11.2, 10.8], [7.0, 7.1, 6.9])
    assert off["n"] == 3
    assert off["mean"] < 0 and off["below_zero"] is True
    assert off["ci95_excludes_zero"] is True          # CI entirely below zero
    assert off["p_value"] is not None and off["p_value"] < 0.05
    assert len(off["per_pair"]) == 3


def test_paired_change_zero_variance_is_definite():
    # every rep moves by the identical fraction: zero variance, a definite result, p == 0.
    r = PC.paired_change([10.0, 20.0], [5.0, 10.0])   # -50% both reps
    assert r["mean"] == -50.0 and r["ci95_half"] == 0.0
    assert r["ci95_excludes_zero"] is True and r["p_value"] == 0.0


def test_paired_change_needs_two_reps():
    r = PC.paired_change([11.0], [7.0])
    assert r["n"] == 1
    assert r["mean"] is None and r["ci95_half"] is None
    assert r["p_value"] is None and r["ci95_excludes_zero"] is None


def test_paired_change_noise_not_significant():
    # arms overlap heavily: the paired CI straddles zero, so no significant saving.
    r = PC.paired_change([10.0, 10.0, 10.0], [9.9, 10.2, 9.8])
    assert r["n"] == 3
    assert r["ci95_excludes_zero"] is False


def test_evaluate_ab_headline_carries_paired_energy_stats():
    off = PC.arm_summary("off", _rows([11.0, 11.2, 10.8]), "MEASURED")
    on = PC.arm_summary("on", _rows([7.0, 7.1, 6.9]), "MEASURED")
    ab = PC.evaluate_ab(off, on)
    h = ab["headline"]
    assert "j_per_req_change_pct_paired" in h and "j_per_correct_change_pct_paired" in h
    paired = h["j_per_req_change_pct_paired"]
    assert paired["n"] == 3 and paired["ci95_excludes_zero"] is True
    assert h["energy_saving_significant"] is True
    # the point estimate is still there, now backed by the paired CI
    assert h["j_per_correct_change_pct"] < 0


def test_evaluate_ab_single_rep_degrades_without_crashing():
    # the two existing gate tests pass one row per arm; the paired stats must degrade to None.
    off = PC.arm_summary("off", _rows([11.0]), "MEASURED")
    on = PC.arm_summary("on", _rows([7.0]), "MEASURED")
    ab = PC.evaluate_ab(off, on)
    assert ab["headline"]["j_per_req_change_pct_paired"]["p_value"] is None
    assert ab["headline"]["energy_saving_significant"] is False


def test_run_ab_paired_stats_survive_interleaving():
    # run_ab appends off/on in rep order even though it rotates arm order; pairing must hold.
    def trial(on, rep):
        return {"j_per_req": (7.0 if on else 11.0) + 0.1 * rep, "pass_pct": 96.0,
                "ttft_ms": 120 if on else 400, "p99_ms": 3000, "cache_hit_rate": 0.8 if on else 0.0,
                "reasoning_measured": True}
    ab = PC.run_ab(trial, reps=3, label="MEASURED")
    paired = ab["headline"]["j_per_req_change_pct_paired"]
    assert paired["n"] == 3 and paired["below_zero"] is True
    assert ab["headline"]["energy_saving_significant"] is True


def test_mock_combined_summary_has_paired_stats():
    ab = PC.mock_ab(reps=2)   # the retest combined block runs --reps 2
    h = ab["headline"]
    assert h["j_per_req_change_pct_paired"]["n"] == 2
    assert h["energy_saving_significant"] is True


def test_errors_on_an_arm_block_the_gate():
    # a request error on the stock/off arm lowers its pass rate and flatters the on arm's
    # J/correct saving; mirror the sweep and refuse to "keep" when either arm errored.
    off = PC.arm_summary("off", [{"j_per_req": 11.0, "pass_pct": 90.0, "ttft_ms": 400,
                                   "p99_ms": 3000, "cache_hit_rate": 0.0, "errors": 2}], "MEASURED")
    on = PC.arm_summary("on", [{"j_per_req": 7.0, "pass_pct": 96.0, "ttft_ms": 120,
                                 "p99_ms": 2900, "cache_hit_rate": 0.8, "errors": 0}], "MEASURED")
    ab = PC.evaluate_ab(off, on)
    assert ab["kept"] is False and any("request errors" in f for f in ab["gate"]["failed"])
    assert ab["gate"]["errors"] == {"off": 2, "on": 0}


def test_no_errors_does_not_trip_the_error_gate():
    # the common case (no errors key / zero errors) must not change the verdict.
    off = PC.arm_summary("off", _rows([11.0, 11.2]), "MEASURED")
    on = PC.arm_summary("on", _rows([7.0, 7.1]), "MEASURED")
    ab = PC.evaluate_ab(off, on)
    assert ab["kept"] is True and ab["gate"]["errors"] == {"off": 0, "on": 0}
