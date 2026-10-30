"""'Wonder reads the chart': point normalisation, stable hashes, the PNG
render, the deterministic fallback, the (faked) vision read path, the receipt
block and the --fake runner. No network, no paid calls — every client here is a
plain inline function. Everything plotted is SYNTHETIC, never a measurement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wattnot.receipt import build_receipt, verify_hash
from wattnot.wonderchart.chart import (
    ChartPoint,
    build_points,
    chart_png_bytes,
    chart_sha256,
    points_sha256,
    render_chart,
)
from wattnot.wonderchart.reader import (
    ChartSuggestion,
    fallback_suggestion,
    read_chart,
    wonder_chart_entry,
)
from wattnot.wonderchart.run import _DEMO, main

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "receipts"


# --- build_points -----------------------------------------------------------

def test_build_points_planner_shape():
    trials = [
        {"cap_w": 375, "clock_profile": "base", "joules_per_correct": 1.0,
         "quality": 0.99, "kept": True, "label": "SYNTHETIC"},
        {"cap_w": 300, "clock_profile": "base", "joules_per_correct": 0.95,
         "quality": 0.88, "kept": False},
    ]
    pts = build_points(trials)
    assert [type(p) for p in pts] == [ChartPoint, ChartPoint]
    assert pts[0].cap_w == 375 and pts[0].clock_profile == "base"
    assert pts[0].joules_per_correct == 1.0
    assert pts[0].quality == 0.99 and pts[0].kept is True
    assert pts[0].label == "SYNTHETIC"
    # quality already a fraction (<=1) is left alone
    assert pts[1].quality == 0.88 and pts[1].kept is False
    # label defaults to MEASURED when absent
    assert pts[1].label == "MEASURED"


def test_build_points_harness_shape_normalises():
    # harness shape: energy_change_pct / quality_pass_pct / accepted
    trials = [
        {"power_cap_w": 350, "clock_profile": "base", "energy_change_pct": -10.0,
         "quality_pass_pct": 97.0, "accepted": True},
        {"power_cap_w": 300, "energy_change_pct": 5.0,
         "quality_pass_pct": 100.0, "accepted": False},
    ]
    pts = build_points(trials)
    # jpc derived from energy_change_pct: 1 + (-10/100) = 0.9
    assert pts[0].joules_per_correct == pytest.approx(0.9)
    # quality > 1 (a pass percentage) is folded to a 0..1 fraction
    assert pts[0].quality == pytest.approx(0.97)
    # kept maps from accepted
    assert pts[0].kept is True
    assert pts[0].cap_w == 350
    assert pts[1].joules_per_correct == pytest.approx(1.05)
    assert pts[1].quality == pytest.approx(1.0)
    assert pts[1].kept is False


def test_build_points_defaults_for_empty_trial():
    pts = build_points([{}])
    p = pts[0]
    assert p.cap_w == 0
    assert p.clock_profile == "base"
    assert p.joules_per_correct == pytest.approx(1.0)  # 1 + 0/100
    assert p.quality == pytest.approx(1.0)
    assert p.kept is False
    # a synthesised setting from cap/clock
    assert p.setting == "0W/base"


# --- hashes -----------------------------------------------------------------

def test_points_and_chart_hashes_are_stable_and_input_sensitive():
    a = build_points(_DEMO)
    b = build_points(_DEMO)
    # same input -> same hash
    assert points_sha256(a) == points_sha256(b)
    # different input -> different hash
    changed = build_points(_DEMO[:-1])
    assert points_sha256(changed) != points_sha256(a)

    blob = b"\x89PNG\r\n\x1a\n some bytes"
    assert chart_sha256(blob) == chart_sha256(blob)
    assert chart_sha256(blob) != chart_sha256(blob + b"x")
    # the hashes are plain hex sha256 digests
    assert len(points_sha256(a)) == 64
    assert len(chart_sha256(blob)) == 64


# --- PNG render -------------------------------------------------------------

def test_chart_png_bytes_is_a_png():
    pytest.importorskip("PIL")
    png = chart_png_bytes(build_points(_DEMO))
    assert isinstance(png, bytes)
    assert png.startswith(b"\x89PNG")


def test_render_chart_writes_file(tmp_path):
    pytest.importorskip("PIL")
    out = render_chart(build_points(_DEMO), tmp_path / "sub" / "chart.png")
    assert out.exists()
    assert out.read_bytes().startswith(b"\x89PNG")


# --- fallback ---------------------------------------------------------------

def test_fallback_suggestion_shape():
    pts = build_points(_DEMO)
    s = fallback_suggestion(pts)
    assert isinstance(s, ChartSuggestion)
    assert s.source == "fallback"
    assert s.model == "deterministic"
    assert s.action in {"try", "stop"}
    if s.action == "try":
        tried = {(p.cap_w, p.clock_profile) for p in pts}
        pick = (s.cap_w, s.clock_profile)
        assert pick not in tried
        from wattnot.proof.wonder_planner import candidates
        pairs = [(int(c), str(p)) for c, p in candidates()]
        assert pick in pairs


# --- read_chart (faked client) ----------------------------------------------

def _wonder_response(payload: dict, *, prompt_tokens: int = 120,
                     completion_tokens: int = 40) -> dict:
    """A well-formed Muse-Wonder-shaped response wrapping ``payload`` as JSON."""
    return {
        "choices": [{"message": {"content": json.dumps(payload)}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def test_read_chart_no_client_falls_back():
    pts = build_points(_DEMO)
    s = read_chart(pts, png=b"\x89PNG", client=None)
    assert s.source == "fallback"


def test_read_chart_no_png_falls_back():
    pts = build_points(_DEMO)
    called = []

    def client(body, *, purpose):  # pragma: no cover - must never be called
        called.append(purpose)
        return _wonder_response({"action": "stop"})

    s = read_chart(pts, png=None, client=client)
    assert s.source == "fallback"
    assert called == []  # no png -> no client call at all


def test_read_chart_picks_an_available_candidate():
    pts = build_points(_DEMO)
    tried = {(p.cap_w, p.clock_profile) for p in pts}
    from wattnot.proof.wonder_planner import candidates
    pairs = [(int(c), str(p)) for c, p in candidates()]
    cap, prof = next(c for c in pairs if c not in tried)

    def client(body, *, purpose):
        assert purpose.startswith("wonder-chart:")
        return _wonder_response(
            {"action": "try", "cap_w": cap, "clock_profile": prof,
             "reason": "lower joules, quality held"})

    s = read_chart(pts, png=b"\x89PNG", client=client)
    assert s.source == "wonder"
    assert s.action == "try"
    assert (s.cap_w, s.clock_profile) == (cap, prof)
    assert s.tokens == {"input": 120, "output": 40}


def test_read_chart_rejects_non_candidate_and_falls_back():
    pts = build_points(_DEMO)

    def client(body, *, purpose):
        # a setting that is not in the available candidate list
        return _wonder_response(
            {"action": "try", "cap_w": 999, "clock_profile": "turbo",
             "reason": "off the allowed list"})

    s = read_chart(pts, png=b"\x89PNG", client=client)
    assert s.source == "fallback"


def test_read_chart_rejects_already_tried_and_falls_back():
    pts = build_points(_DEMO)
    already = (pts[0].cap_w, pts[0].clock_profile)  # already on the chart

    def client(body, *, purpose):
        return _wonder_response(
            {"action": "try", "cap_w": already[0], "clock_profile": already[1],
             "reason": "already plotted"})

    s = read_chart(pts, png=b"\x89PNG", client=client)
    assert s.source == "fallback"


def test_read_chart_invalid_json_falls_back():
    pts = build_points(_DEMO)

    def client(body, *, purpose):
        return {"choices": [{"message": {"content": "not json {"}}]}

    assert read_chart(pts, png=b"\x89PNG", client=client).source == "fallback"


def test_read_chart_raising_client_falls_back():
    pts = build_points(_DEMO)

    def client(body, *, purpose):
        raise RuntimeError("boom")

    assert read_chart(pts, png=b"\x89PNG", client=client).source == "fallback"


def test_read_chart_stop_from_wonder():
    pts = build_points(_DEMO)

    def client(body, *, purpose):
        return _wonder_response(
            {"action": "stop", "cap_w": None, "clock_profile": None,
             "reason": "front has flattened"})

    s = read_chart(pts, png=b"\x89PNG", client=client)
    assert s.source == "wonder"
    assert s.action == "stop"
    assert s.cap_w is None and s.clock_profile is None


# --- wonder_chart_entry -----------------------------------------------------

def test_wonder_chart_entry_fields_and_hash_fallback():
    pts = build_points(_DEMO)
    s = fallback_suggestion(pts)
    entry = wonder_chart_entry(pts, s)
    assert entry["label"] == "SYNTHETIC"
    assert entry["est_wh"]["label"] == "ESTIMATE"
    # no chart_sha256 passed -> falls back to the points hash
    assert entry["chart_sha256"] == points_sha256(pts)
    assert entry["offline_fallback"] is True  # fallback source
    assert len(entry["points"]) == len(pts)
    assert entry["source"] == "fallback"


def test_wonder_chart_entry_uses_given_chart_hash_and_not_offline():
    pts = build_points(_DEMO)
    s = ChartSuggestion("try", 375, "locked-high", "Wonder read it",
                        "wonder", "trace-1", "muse-wonder-1.0")
    sha = "a" * 64
    entry = wonder_chart_entry(pts, s, chart_sha256=sha)
    assert entry["chart_sha256"] == sha
    assert entry["offline_fallback"] is False  # source == "wonder"
    assert entry["suggestion"]["action"] == "try"
    assert entry["suggestion"]["cap_w"] == 375


def test_wonder_chart_entry_truncates_long_reason():
    pts = build_points(_DEMO)
    s = ChartSuggestion("stop", None, None, "x" * 500,
                        "wonder", "t" * 500, "m" * 500)
    entry = wonder_chart_entry(pts, s)
    assert len(entry["reason"]) <= 195
    assert len(entry["trace_id"]) <= 80
    assert len(entry["model"]) <= 64


# --- run.main ---------------------------------------------------------------

def test_main_fake_returns_zero():
    assert main(["--fake"]) == 0


def test_main_fake_writes_outputs(tmp_path):
    pytest.importorskip("PIL")
    rc = main(["--fake", "--out", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "wonder_chart.png").exists()
    summary = json.loads((tmp_path / "wonder_chart.json").read_text())
    for key in ("spec", "mode", "source", "suggestion", "wonder_chart"):
        assert key in summary
    assert summary["mode"] == "fake"
    assert summary["source"] == "fallback"


def test_main_fake_with_sweep_file(tmp_path):
    sweep = tmp_path / "sweep.json"
    sweep.write_text(json.dumps({"trials": _DEMO}))
    assert main(["--fake", "--sweep", str(sweep)]) == 0


def test_main_fake_with_bare_list_sweep(tmp_path):
    sweep = tmp_path / "sweep.json"
    sweep.write_text(json.dumps(_DEMO))
    assert main(["--fake", "--sweep", str(sweep)]) == 0


def test_main_missing_sweep_file_returns_one(tmp_path):
    assert main(["--fake", "--sweep", str(tmp_path / "nope.json")]) == 1


def test_main_bad_sweep_file_returns_one(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json {")
    assert main(["--fake", "--sweep", str(bad)]) == 1


# --- receipt integration ----------------------------------------------------

@pytest.fixture
def base_run():
    valid = json.loads((FIXTURES / "valid.json").read_text())
    return {
        "run_id": "run_20261030_160000_wonder",
        "subject": valid["subject"],
        "baseline": valid["baseline"],
        "candidate": valid["candidate"],
        "measurement": {
            "meter": "NVML internal energy counter via pynvml", "hz": 10.0,
            "label": "MEASURED",
            "csv": [{"role": "candidate", "path": "cand.csv", "sha256": "0" * 64}],
        },
        "planner_trace": valid["planner_trace"],
    }


def test_receipt_wonder_chart_block_builds_and_verifies(base_run):
    pts = build_points(_DEMO)
    s = fallback_suggestion(pts)
    entry = wonder_chart_entry(pts, s)
    receipt = build_receipt(base_run, wonder_chart=entry)
    block = receipt["measurement"]["wonder_chart"]
    assert block["label"] == "SYNTHETIC"
    assert block["est_wh"]["label"] == "ESTIMATE"
    assert block["points"]
    # a passing build proves lint + schema accepted the block; confirm the hash too
    ok, want, got = verify_hash(receipt)
    assert ok, (want, got)


def test_receipt_wonder_chart_via_run_dict(base_run):
    pts = build_points(_DEMO)
    s = fallback_suggestion(pts)
    run = dict(base_run)
    run["wonder_chart"] = wonder_chart_entry(pts, s)
    receipt = build_receipt(run)
    assert receipt["measurement"]["wonder_chart"]["label"] == "SYNTHETIC"
    assert verify_hash(receipt)[0]
