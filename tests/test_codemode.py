"""Code Mode detection: detection logic, the before/after math, honest labels, the receipt."""

from __future__ import annotations

import json

import pytest

from wattnot.agentwaste.codemode import (
    CodeModeConfig,
    analyze,
    build_receipt,
    compare,
    detect,
    load_run,
    measure,
    model_after,
)
from wattnot.receipt.canon import canonical_sha256
from wattnot.receipt.sign import generate_test_key, sign_run_receipt, verify_offline_bundle


def _trace_run(task, n_tools, n_trips, prompt=10000, completion=2000, wall=20.0, joules=None):
    """A synthetic recorded run: n_trips steps, each calling a distinct subset of n_tools."""
    tools = [f"tool_{i}" for i in range(n_tools)]
    trace = []
    for step in range(n_trips):
        # spread the tools across steps so the union is exactly n_tools
        name = tools[step % n_tools]
        trace.append({"n": step + 1, "calls": [[name, "{}", "ok"]], "tokens": 100})
    # make sure every tool appears at least once
    if n_trips and n_tools > n_trips:
        trace[-1]["calls"] = [[t, "{}", "ok"] for t in tools]
    d = {"case": task, "trace": trace, "prompt_tokens": prompt,
         "completion_tokens": completion, "total_tokens": prompt + completion, "wall_s": wall}
    if joules is not None:
        d["joules"] = joules
    return d


# ---- detection ------------------------------------------------------------------------------

def test_detect_needs_both_big_list_and_many_trips():
    cfg = CodeModeConfig(min_tools=5, min_round_trips=6)
    big_and_many = load_run(_trace_run("a", n_tools=8, n_trips=10))
    assert detect(big_and_many, cfg).candidate is True

    big_few_trips = load_run(_trace_run("b", n_tools=8, n_trips=2))
    assert detect(big_few_trips, cfg).candidate is False  # one big tool list, few trips

    small_many_trips = load_run(_trace_run("c", n_tools=2, n_trips=20))
    assert detect(small_many_trips, cfg).candidate is False  # long single-tool loop


def test_detect_reasons_mention_both_thresholds():
    cfg = CodeModeConfig(min_tools=5, min_round_trips=6)
    det = detect(load_run(_trace_run("a", 8, 10)), cfg)
    joined = " ".join(det.reasons)
    assert "tool list" in joined and "round trips" in joined


def test_round_trips_and_tools_counted_from_trace():
    # 3 steps make calls, 1 step is text-only (no calls) -> 3 round trips, 2 distinct tools
    d = {"case": "x", "trace": [
        {"calls": [["a", "{}", "ok"]]},
        {"calls": []},
        {"calls": [["b", "{}", "ok"], ["a", "{}", "ok"]]},
        {"calls": [["b", "{}", "ok"]]},
    ], "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    run = load_run(d)
    assert run.round_trips == 3
    assert run.tools_seen == 2


def test_tools_offered_overrides_seen_floor():
    d = _trace_run("x", n_tools=3, n_trips=8)
    d["tools_offered"] = 40
    run = load_run(d)
    assert run.tools == 40 and run.tools_measured is True
    seen = load_run(_trace_run("y", n_tools=3, n_trips=8))
    assert seen.tools == 3 and seen.tools_measured is False


# ---- the math -------------------------------------------------------------------------------

def test_schema_tokens_resent_scales_with_round_trips():
    cfg = CodeModeConfig(schema_tokens_per_tool=100)
    run = load_run(_trace_run("x", n_tools=10, n_trips=8))
    m = measure(run, cfg)
    assert m["schema_tokens_once"]["value"] == 10 * 100
    assert m["schema_tokens_resent"]["value"] == 10 * 100 * 8  # sent on every round trip


def test_after_sends_schema_once_and_collapses_round_trips():
    cfg = CodeModeConfig(schema_tokens_per_tool=100, after_round_trips=1)
    run = load_run(_trace_run("x", n_tools=10, n_trips=8))
    a = model_after(run, cfg)
    assert a["round_trips"]["value"] == 1
    assert a["schema_tokens_resent"]["value"] == 10 * 100  # once, not x8


def test_token_saving_is_only_the_schema_resend_and_clamped():
    cfg = CodeModeConfig(schema_tokens_per_tool=100, after_round_trips=1)
    run = load_run(_trace_run("x", n_tools=10, n_trips=8, prompt=10000, completion=2000))
    c = compare(run, cfg)
    saved_schema = 100 * 10 * (8 - 1)  # 7000
    assert c["saved"]["schema_tokens_resent"]["value"] == saved_schema
    # total tokens drop by exactly the schema we stop resending (conservative)
    assert c["before"]["total_tokens"]["value"] - c["after"]["total_tokens"]["value"] == saved_schema


def test_total_after_never_below_completion_tokens():
    # a wildly large per-tool schema would over-subtract; the model must clamp.
    cfg = CodeModeConfig(schema_tokens_per_tool=100000, after_round_trips=1)
    run = load_run(_trace_run("x", n_tools=10, n_trips=8, prompt=1000, completion=500))
    a = model_after(run, cfg)
    assert a["total_tokens"]["value"] == 500  # clamped to completion, not negative


def test_wall_time_scales_by_round_trip_ratio():
    cfg = CodeModeConfig(after_round_trips=1)
    run = load_run(_trace_run("x", n_tools=10, n_trips=10, wall=20.0))
    a = model_after(run, cfg)
    assert a["wall_s"]["value"] == pytest.approx(20.0 * 1 / 10)


def test_dollar_saving_is_nonnegative():
    cfg = CodeModeConfig()
    run = load_run(_trace_run("x", n_tools=10, n_trips=8))
    c = compare(run, cfg)
    assert c["saved"]["usd"]["value"] >= 0


# ---- honest labels --------------------------------------------------------------------------

def test_measured_vs_estimate_labels():
    cfg = CodeModeConfig()
    run = load_run(_trace_run("x", n_tools=10, n_trips=8))
    m = measure(run, cfg)
    assert m["round_trips"]["label"] == "MEASURED"
    assert m["total_tokens"]["label"] == "MEASURED"
    assert m["schema_tokens_resent"]["label"] == "ESTIMATE"  # per-tool size is assumed
    assert m["usd"]["label"] == "ESTIMATE"
    a = model_after(run, cfg)
    assert a["round_trips"]["label"] == "ESTIMATE"  # modelled after
    assert a["total_tokens"]["label"] == "ESTIMATE"


def test_energy_before_is_estimate_but_after_is_pending_not_invented():
    cfg = CodeModeConfig()
    run = load_run(_trace_run("x", n_tools=10, n_trips=8))  # no measured joules
    m = measure(run, cfg)
    a = model_after(run, cfg)
    assert m["joules"]["label"] == "ESTIMATE" and m["joules"]["value"] is not None
    # prompt-side energy has no measured rate -> pending, never a made-up number
    assert a["joules"]["value"] is None and a["joules"]["label"] == "PENDING"
    assert compare(run, cfg)["saved"]["joules"]["value"] is None


def test_measured_joules_kept_when_trace_has_them():
    cfg = CodeModeConfig()
    run = load_run(_trace_run("x", n_tools=10, n_trips=8, joules=123.4))
    m = measure(run, cfg)
    assert m["joules"]["value"] == 123.4 and m["joules"]["label"] == "MEASURED"


def test_tools_label_marks_floor_when_only_seen():
    cfg = CodeModeConfig()
    run = load_run(_trace_run("x", n_tools=10, n_trips=8))
    assert measure(run, cfg)["tools"]["label"] == "MEASURED-floor"
    d = _trace_run("y", n_tools=3, n_trips=8)
    d["tools_offered"] = 20
    assert measure(load_run(d), cfg)["tools"]["label"] == "MEASURED"


# ---- config ---------------------------------------------------------------------------------

def test_config_from_dict_rejects_unknown_keys():
    with pytest.raises(ValueError):
        CodeModeConfig.from_dict({"min_tools": 3, "bogus": 1})
    cfg = CodeModeConfig.from_dict({"min_tools": 3, "min_round_trips": 2})
    assert cfg.min_tools == 3 and cfg.min_round_trips == 2


def test_thresholds_are_configurable():
    runs = [_trace_run("x", n_tools=3, n_trips=3)]
    strict = analyze(runs, CodeModeConfig(min_tools=5, min_round_trips=6))
    loose = analyze(runs, CodeModeConfig(min_tools=2, min_round_trips=2))
    assert strict["candidates"] == 0
    assert loose["candidates"] == 1


# ---- analyze over the real recorded traces --------------------------------------------------

def test_analyze_on_recorded_traces_finds_candidates(tmp_path):
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    runs = [json.loads(l) for l in (root / "data/agent-waste/v3/runs.jsonl").read_text().splitlines() if l.strip()]
    res = analyze(runs, CodeModeConfig())
    assert res["spec"] == "wattnot.codemode/1"
    assert res["runs_examined"] == len(runs)
    assert res["candidates"] >= 1
    # saved round trips is a non-negative integer
    assert res["totals"]["round_trips"]["saved"]["value"] >= 0
    # notes stay honest about the model and pending energy
    joined = " ".join(res["notes"]).lower()
    assert "estimate" in joined and "sandbox" in joined


# ---- receipt --------------------------------------------------------------------------------

def test_receipt_hash_matches_and_signs(tmp_path):
    runs = [_trace_run("x", n_tools=10, n_trips=8)]
    res = analyze(runs, CodeModeConfig())
    rec = build_receipt(res, source="unit test", now="2026-10-29T00:00:00Z")
    assert rec["spec"] == "wattnot.codemode-receipt/1"
    assert rec["label"] == "REPLAY"
    # integrity hash is over everything but the integrity block
    assert rec["integrity"]["sha256"] == canonical_sha256(rec)

    path = tmp_path / "codemode.json"
    path.write_text(json.dumps(rec, indent=2) + "\n")
    key = generate_test_key(tmp_path / "key.pem")
    bundle, signer = sign_run_receipt(path, key_path=key)
    assert signer.startswith("offline:")
    signed = json.loads(path.read_text())
    ok, msg = verify_offline_bundle(signed, json.loads(bundle.read_text()))
    assert ok, msg


def test_receipt_carries_before_after_rows():
    runs = [_trace_run("x", n_tools=10, n_trips=8)]
    res = analyze(runs, CodeModeConfig())
    rec = build_receipt(res, source="unit test", now="2026-10-29T00:00:00Z")
    assert rec["summary"]["candidates"] == 1
    row = rec["rows"][0]
    assert "before" in row and "after" in row and "saved" in row
    assert row["before"]["round_trips"]["value"] > row["after"]["round_trips"]["value"]
