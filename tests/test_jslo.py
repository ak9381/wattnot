"""Tests for the Joule-SLO admission controller (CPU-only, no GPU/network/money)."""

from __future__ import annotations

import json

import pytest

from wattnot.longrun import knee
from wattnot.longrun.jslo import admit, main
from wattnot.longrun.knee import Point

# RUN2 (MEASURED): J/correct by cap. 325 W fails the gate; 350 W is the floor under a 10% p99.
#   uncapped 17.03, 500->16.38, 450->14.69, 400->13.81, 350->12.42, 325->11.84(FAIL)


def test_generous_budget_admits_the_least_aggressive_config():
    # A budget any config beats -> serve the safest (highest-watt) one, not the greenest.
    out = admit(knee.RUN2_SWEEP, budget_j_per_correct=20.0)
    assert out["verdict"] == "admit"
    assert out["chosen"] is None          # uncapped is the least aggressive serveable config
    assert out["greenest"] == 350         # but 350 W is the greenest that still passes
    assert out["achieved_j_per_correct"] == 17.03
    assert out["label"] == "MEASURED"


def test_tight_budget_picks_the_least_aggressive_config_that_still_meets_it():
    # 14.0 J/correct: 450W(14.69) misses, 400W(13.81) meets -> choose 400 W, not 350 W.
    out = admit(knee.RUN2_SWEEP, budget_j_per_correct=14.0)
    assert out["verdict"] == "admit"
    assert out["chosen"] == 400
    assert out["achieved_j_per_correct"] == 13.81
    assert out["headroom_j"] == pytest.approx(14.0 - 13.81, abs=1e-6)
    assert out["greenest"] == 350


def test_budget_below_the_floor_is_infeasible_and_reports_the_floor():
    # 12.0 J/correct is under the 350 W floor (12.42); 325 W (11.84) is gate-failed, so no help.
    out = admit(knee.RUN2_SWEEP, budget_j_per_correct=12.0)
    assert out["verdict"] == "infeasible"
    assert out["chosen"] is None
    assert out["floor_j_per_correct"] == 12.42
    assert out["greenest"] == 350
    assert "floor" in out["reason"]


def test_budget_exactly_on_a_config_is_admitted():
    out = admit(knee.RUN2_SWEEP, budget_j_per_correct=13.81)
    assert out["verdict"] == "admit"
    assert out["chosen"] == 400
    assert out["headroom_j"] == 0.0


def test_relaxing_p99_unlocks_the_gate_failed_config_only_if_it_also_passes_quality():
    # 325 W fails the *quality* gate, so even a lax p99 ceiling must not let it back the SLO.
    out = admit(knee.RUN2_SWEEP, budget_j_per_correct=12.0, p99_ceiling_pct=99.0)
    assert out["floor_j_per_correct"] == 12.42   # still 350 W, never 325 W
    assert out["verdict"] == "infeasible"


def test_p99_ceiling_excludes_configs_that_breach_latency():
    # With a 2% p99 ceiling only uncapped/500/450 qualify; 450 W (14.69) is the floor.
    out = admit(knee.RUN2_SWEEP, budget_j_per_correct=13.0, p99_ceiling_pct=2.0)
    assert out["verdict"] == "infeasible"
    assert out["floor_j_per_correct"] == 14.69
    assert out["greenest"] == 450


def test_no_serveable_config_is_infeasible_with_a_clear_reason():
    pts = [Point(None, 10.0, 0.0, gate_pass=False), Point(400, 9.0, 1.0, gate_pass=False)]
    out = admit(pts, budget_j_per_correct=100.0)
    assert out["verdict"] == "infeasible"
    assert out["chosen"] is None and out["floor_j_per_correct"] is None
    assert "gate" in out["reason"]


def test_missing_p99_on_a_config_keeps_it_out_of_the_slo():
    # A config with no measured p99 cannot back a latency SLO, so it is never chosen.
    pts = [Point(None, 10.0, 0.0, True), Point(400, 8.0, None, True)]
    out = admit(pts, budget_j_per_correct=9.0)
    assert out["verdict"] == "infeasible"          # only uncapped (10.0) serveable, over budget
    assert out["floor_j_per_correct"] == 10.0


def test_label_is_carried_through_from_the_points():
    pts = [Point(None, 10.0, 0.0, True, label="ESTIMATE"),
           Point(400, 8.0, 1.0, True, label="ESTIMATE")]
    out = admit(pts, budget_j_per_correct=9.0)
    assert out["label"] == "ESTIMATE"
    assert out["verdict"] == "admit" and out["chosen"] == 400


def test_cli_admit_prints_verdict_and_returns_zero(capsys):
    rc = main(["--demo", "--budget", "14.0"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ADMIT" in out and "400 W" in out


def test_cli_infeasible_returns_nonzero(capsys):
    rc = main(["--demo", "--budget", "12.0"])
    assert rc == 1
    assert "INFEASIBLE" in capsys.readouterr().out


def test_cli_reads_a_sweep_file_and_emits_json(tmp_path, capsys):
    f = tmp_path / "sweep.json"
    f.write_text(json.dumps({"points": knee.RUN2_SWEEP}))
    rc = main(["--sweep", str(f), "--budget", "13.9", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "admit" and out["chosen"] == 400


# ---- Cost budget (USD per 1k correct). RUN2 throughput is *synthetic* (ESTIMATE, correct/hour)
#   by cap: uncapped 1600, 500->1550, 450->1450, 400->1380, 350->1240. At $2.50/h that is cost/1k
#   of uncapped 1.5625, 500 1.6129, 450 1.7241, 400 1.8116, 350 2.0161 - a lower cap costs *more*
#   money per answer even as it saves joules, so joule and cost budgets pull opposite ways. Because
#   the throughput is not measured, every RUN2 cost is an ESTIMATE carrying the throughput note.

THROUGHPUT_NOTE = "throughput not yet measured; real numbers come from the H100 run"


def test_no_cost_budget_leaves_cost_fields_none_and_joule_behaviour_unchanged():
    out = admit(knee.RUN2_SWEEP, budget_j_per_correct=14.0)
    assert out["verdict"] == "admit" and out["chosen"] == 400
    assert out["binds"] == "joule"
    assert out["achieved_usd_per_1k_correct"] is None and out["cost_basis"] is None
    assert out["cost_floor_usd_per_1k_correct"] is None


def test_price_without_a_cost_budget_reports_cost_but_does_not_enforce_it():
    # A $/h with no cost budget prices the chosen config and the floor, but never gates on it. The
    # RUN2 throughput is synthetic, so the cost is an ESTIMATE carrying the throughput note.
    out = admit(knee.RUN2_SWEEP, budget_j_per_correct=14.0, usd_per_hour=2.5)
    assert out["verdict"] == "admit" and out["chosen"] == 400 and out["binds"] == "joule"
    assert out["achieved_usd_per_1k_correct"] == pytest.approx(2.5 / 1380 * 1000, abs=1e-4)
    assert out["cost_basis"] == "ESTIMATE"
    assert out["cost_note"] == THROUGHPUT_NOTE
    assert out["cost_floor_usd_per_1k_correct"] == pytest.approx(2.5 / 1600 * 1000, abs=1e-4)
    assert out["cost_floor_basis"] == "ESTIMATE"


def test_cost_budget_binds_when_it_is_the_tighter_constraint():
    # Joule budget 15 admits 450/400/350; cost budget $1.75 admits uncapped/500/450; the least
    # aggressive config meeting both is 450 W, and cost sits closer to its budget than joules do.
    out = admit(knee.RUN2_SWEEP, budget_j_per_correct=15.0,
                budget_usd_per_1k_correct=1.75, usd_per_hour=2.5)
    assert out["verdict"] == "admit" and out["chosen"] == 450
    assert out["binds"] == "cost"
    assert out["achieved_usd_per_1k_correct"] == pytest.approx(2.5 / 1450 * 1000, abs=1e-4)
    assert out["headroom_usd"] == pytest.approx(1.75 - 2.5 / 1450 * 1000, abs=1e-4)


def test_joule_budget_binds_when_it_is_the_tighter_constraint():
    out = admit(knee.RUN2_SWEEP, budget_j_per_correct=14.0,
                budget_usd_per_1k_correct=1.9, usd_per_hour=2.5)
    assert out["verdict"] == "admit" and out["chosen"] == 400
    assert out["binds"] == "joule"
    assert out["cost_basis"] == "ESTIMATE"        # RUN2 throughput is synthetic


def test_cost_below_the_cost_floor_is_infeasible_and_blames_cost():
    # Joule budget wide open (100), but no config is under $1.40/1k - the cheapest is $1.5625.
    out = admit(knee.RUN2_SWEEP, budget_j_per_correct=100.0,
                budget_usd_per_1k_correct=1.4, usd_per_hour=2.5)
    assert out["verdict"] == "infeasible" and out["binds"] == "cost"
    assert out["cost_floor_usd_per_1k_correct"] == pytest.approx(2.5 / 1600 * 1000, abs=1e-4)
    assert "cost floor" in out["reason"]


def test_disjoint_budgets_bind_together():
    # Joule budget 16 admits only 450/400/350 (cheap-but-greedy configs excluded); cost budget
    # $1.70 admits only uncapped/500 - the two sets never overlap, so both budgets bind.
    out = admit(knee.RUN2_SWEEP, budget_j_per_correct=16.0,
                budget_usd_per_1k_correct=1.7, usd_per_hour=2.5)
    assert out["verdict"] == "infeasible" and out["binds"] == "both"
    assert out["floor_j_per_correct"] == 12.42
    assert "both budgets" in out["reason"]


def test_cost_label_is_measured_derived_only_when_the_throughput_is_measured_too():
    # A measured point with a *measured* throughput priced at a list $/h is MEASURED-derived (a
    # measured time at a list price), never a bare MEASURED - and carries no throughput note.
    pts = [Point(None, 10.0, 0.0, True, good_per_hour=1000.0, throughput_label="MEASURED")]
    out = admit(pts, budget_j_per_correct=20.0, usd_per_hour=3.0)
    assert out["cost_basis"] == "MEASURED-derived"
    assert out["cost_note"] is None


def test_measured_joules_with_estimated_throughput_degrade_cost_to_estimate_with_a_note():
    # The RUN2 case in miniature: the joules are MEASURED but the throughput is not, so the cost is
    # an ESTIMATE and the verdict says why. The point's own label stays MEASURED.
    pts = [Point(None, 10.0, 0.0, True, good_per_hour=1000.0, throughput_label="ESTIMATE")]
    out = admit(pts, budget_j_per_correct=20.0, usd_per_hour=2.0)
    assert out["label"] == "MEASURED"
    assert out["achieved_usd_per_1k_correct"] == pytest.approx(2.0, abs=1e-6)
    assert out["cost_basis"] == "ESTIMATE"
    assert out["cost_note"] == THROUGHPUT_NOTE


def test_cost_label_degrades_to_estimate_when_the_point_is_an_estimate():
    pts = [Point(None, 10.0, 0.0, True, label="ESTIMATE", good_per_hour=1000.0)]
    out = admit(pts, budget_j_per_correct=20.0, usd_per_hour=2.0)
    assert out["achieved_usd_per_1k_correct"] == pytest.approx(2.0, abs=1e-6)
    assert out["cost_basis"] == "ESTIMATE"


def test_cost_budget_without_a_price_cannot_be_checked_and_says_so():
    # A cost budget with no $/h (or no throughput) is unpriceable: honest infeasible, not a guess.
    out = admit(knee.RUN2_SWEEP, budget_j_per_correct=20.0, budget_usd_per_1k_correct=1.0)
    assert out["verdict"] == "infeasible" and out["binds"] == "cost"
    assert out["cost_floor_usd_per_1k_correct"] is None
    assert "cannot be checked" in out["reason"]


def test_config_missing_throughput_is_not_priced():
    # Uncapped has no measured throughput; it cannot back a cost SLO, so 400 W is chosen instead.
    pts = [Point(None, 10.0, 0.0, True, good_per_hour=None),
           Point(400, 9.0, 1.0, True, good_per_hour=1200.0)]
    out = admit(pts, budget_j_per_correct=20.0, budget_usd_per_1k_correct=2.0, usd_per_hour=2.0)
    assert out["verdict"] == "admit" and out["chosen"] == 400
    assert out["achieved_usd_per_1k_correct"] == pytest.approx(2.0 / 1200 * 1000, abs=1e-4)


def test_cli_cost_budget_prints_binds_and_returns_zero(capsys):
    rc = main(["--demo", "--budget", "15.0", "--cost-budget", "1.75", "--usd-per-hour", "2.5"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ADMIT" in out and "binds: cost" in out and "450 W" in out
    # RUN2 throughput is synthetic, so the CLI prints the honesty note.
    assert "throughput not yet measured" in out


def test_cli_cost_infeasible_returns_nonzero(capsys):
    rc = main(["--demo", "--budget", "100.0", "--cost-budget", "1.4", "--usd-per-hour", "2.5"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "INFEASIBLE" in out and "binds: cost" in out
