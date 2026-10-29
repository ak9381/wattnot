"""Tests for the marginal-joule knee advisor (CPU-only, no GPU/network/money)."""

from __future__ import annotations

import json

import pytest

from wattnot.longrun import knee
from wattnot.longrun.knee import Point, analyze, main, marginal_steps


def test_run2_recommends_350w_matching_the_kept_cap():
    # RUN2 kept 350 W: it is the lowest cap that passes the gate; 325 W fails on p99.
    out = analyze(knee.RUN2_SWEEP)
    assert out["recommended"] == 350
    assert out["label"] == "MEASURED"
    assert "350" in out["reason"]


def test_steps_are_ordered_high_to_low_and_cover_every_gap():
    out = analyze(knee.RUN2_SWEEP)
    froms = [s["from_w"] for s in out["steps"]]
    tos = [s["to_w"] for s in out["steps"]]
    assert froms == [None, 500, 450, 400, 350]   # uncapped first, then descending
    assert tos == [500, 450, 400, 350, 325]


def test_marginal_values_are_the_plain_arithmetic():
    steps = marginal_steps(knee.RUN2_SWEEP)
    s = next(x for x in steps if x["from_w"] == 500 and x["to_w"] == 450)
    assert s["watts_dropped"] == 50
    # (16.38 - 14.69) / 50
    assert s["saved_per_w"] == pytest.approx((16.38 - 14.69) / 50, abs=1e-6)
    # (1.8 - 0.7) / 50
    assert s["p99_per_w"] == pytest.approx((1.8 - 0.7) / 50, abs=1e-6)


def test_p99_wall_fires_before_a_too_steep_latency_step():
    # The 350 -> 325 step costs 3.2 pts over 25 W = 0.128 %/W, above the 0.1 ceiling,
    # so the wall is the higher cap of that step: 350 W.
    out = analyze(knee.RUN2_SWEEP, p99_ceiling=0.1)
    assert out["p99_wall"] == 350


def test_gate_failure_alone_blocks_a_cap_even_under_the_ceiling():
    # 325 W passes a lax p99 ceiling, but its gate fails, so it is never recommended.
    out = analyze(knee.RUN2_SWEEP, p99_ceiling=10.0)
    assert out["recommended"] == 325 or out["recommended"] == 350
    # Make it unambiguous: with the gate failing and ceiling lax, fall back to 350.
    assert out["recommended"] != 325


def test_all_pass_recommends_the_most_aggressive_cap():
    pts = [
        Point(None, 10.0, 0.0, True),
        Point(500, 9.0, 0.5, True),
        Point(400, 8.0, 1.0, True),
    ]
    out = analyze(pts, p99_ceiling=10.0)
    assert out["recommended"] == 400
    assert "most aggressive" in out["reason"]


def test_knee_detects_diminishing_joule_returns():
    # Big saving on the first drop, tiny ones after -> knee at the first cap after the big step.
    pts = [
        Point(None, 20.0, 0.0, True),
        Point(600, 10.0, 0.0, True),   # 10 J / 100 W = 0.1 per W (the best step)
        Point(500, 9.9, 0.0, True),    # 0.1 J / 100 W = 0.001 per W -> well under half
        Point(400, 9.8, 0.0, True),
    ]
    out = analyze(pts, knee_frac=0.5, p99_ceiling=10.0)
    assert out["knee_cap"] == 600


def test_none_recommended_when_nothing_passes_the_gate():
    pts = [Point(None, 10.0, 0.0, True), Point(400, 9.0, 1.0, False)]
    out = analyze(pts, p99_ceiling=10.0)
    assert out["recommended"] is None
    assert "gate" in out["reason"]


def test_fewer_than_two_points_is_handled():
    assert analyze([]) ["recommended"] is None
    one = analyze([Point(500, 5.0)])
    assert one["recommended"] is None
    assert "two" in one["reason"]


def test_duplicate_caps_are_deduped_keeping_the_first():
    pts = [Point(400, 8.0), Point(400, 99.0), Point(None, 10.0)]
    out = analyze(pts, p99_ceiling=10.0)
    caps = [p["cap_w"] for p in out["points"]]
    assert caps == [None, 400]
    assert out["points"][1]["j_per_good_answer"] == 8.0


def test_missing_joules_do_not_crash_the_marginals():
    pts = [Point(None, None, 0.0, True), Point(500, 9.0, 1.0, True)]
    steps = marginal_steps(pts)
    assert steps[0]["saved_per_w"] is None
    assert steps[0]["p99_per_w"] is not None


def test_from_mapping_accepts_both_joule_key_spellings():
    a = Point.from_mapping({"cap_w": 500, "j_per_good_answer": 12.0})
    b = Point.from_mapping({"cap_w": 500, "j_per_good": 12.0})
    assert a.j_per_good == b.j_per_good == 12.0


def test_throughput_label_round_trips_and_defaults_to_the_point_label():
    # A throughput can carry its own honesty, separate from the joule label; as_dict emits it only
    # when a throughput is present, defaulting to the point's own label when none was given.
    p = Point.from_mapping({"cap_w": 400, "j_per_good": 9.0, "good_per_hour": 1200.0,
                            "throughput_label": "ESTIMATE"})
    assert p.throughput_label == "ESTIMATE"
    assert p.as_dict()["throughput_label"] == "ESTIMATE"
    # Measured point, throughput given but unlabelled -> inherits MEASURED.
    q = Point(400, 9.0, good_per_hour=1200.0)
    assert q.as_dict()["throughput_label"] == "MEASURED"
    # No throughput at all -> no throughput_label key, as before.
    assert "throughput_label" not in Point(400, 9.0).as_dict()


def test_run2_sweep_marks_its_throughput_as_an_estimate():
    # RUN2 throughput is synthetic: the joules are MEASURED but the per-cap throughput is not.
    assert all(p["throughput_label"] == "ESTIMATE" for p in knee.RUN2_SWEEP)
    assert all(p.get("label", "MEASURED") == "MEASURED" for p in knee.RUN2_SWEEP)


def test_cli_demo_prints_recommendation(capsys):
    rc = main(["--demo"])
    assert rc == 0
    assert "350 W" in capsys.readouterr().out


def test_cli_reads_a_sweep_file_and_emits_json(tmp_path, capsys):
    f = tmp_path / "sweep.json"
    f.write_text(json.dumps({"points": knee.RUN2_SWEEP}))
    rc = main(["--sweep", str(f), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["recommended"] == 350
