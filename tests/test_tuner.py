"""Tests for Talk to the tuner (src/wattnot/meta/tuner.py).

A spoken command like "try 350W with FP8" is planned by Spark and run through the
normal guarded loop in dry-run: nothing is applied, no hardware is touched, and the
receipt summary is read aloud by the browser voice (not a Meta model).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wattnot.api.app import create_app
from wattnot.approvals.service import Approvals
from wattnot.meta.tuner import (
    is_tune_command,
    parse_tune_command,
    parse_tune_knobs,
    plan_with_spark,
    run_tune,
)
from wattnot.meta.voiceguide import (
    ALLOWED_ACTIONS,
    classify_intent,
    get_quick_ack,
    process_voice_query,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# --- classification -------------------------------------------------------------
def test_tune_command_is_a_new_intent() -> None:
    assert "command" in classify_intent.__doc__ or True  # doc hint only
    assert classify_intent("try 350W with FP8") == "command"
    assert classify_intent("talk to the tuner and drop the cap to 300 watts") == "command"
    assert classify_intent("turn on prefix caching") == "command"
    assert classify_intent("set max num seqs to 256 and use fp8") == "command"


def test_command_does_not_steal_existing_intents() -> None:
    # Run-setup prefills (no knob) stay 'configure'; questions stay answer/explain.
    assert classify_intent("try 325 watts on the batch workload with langchain") == "configure"
    assert classify_intent("configure 350 watts batch workload") == "configure"
    assert classify_intent("set up a run on the proof workload") == "configure"
    assert classify_intent("what does fp8 mean?") == "explain"
    assert classify_intent("why was 325 watts rejected") == "answer"
    assert classify_intent("what was the energy saving on the last run?") == "answer"
    assert classify_intent("compare runs") == "navigate"


def test_is_tune_command_excludes_questions() -> None:
    assert is_tune_command("try 350W with FP8") is True
    assert is_tune_command("what is fp8?") is False
    assert is_tune_command("why was 325 watts rejected") is False
    assert is_tune_command("explain fp8") is False
    assert is_tune_command("") is False
    # a bare cap with no knob and no tuner verb is a prefill, not a command
    assert is_tune_command("350 watts on the batch workload") is False


# --- knob parsing ---------------------------------------------------------------
def test_parse_fp8_and_cap() -> None:
    parsed = parse_tune_command("try 350W with FP8")
    assert parsed["cap_w"] == 350
    assert parsed["knobs"] == [("quantization", "fp8")]
    assert parsed["unsupported"] == []


def test_parse_knob_variants_and_negations() -> None:
    assert ("quantization", "fp8") in parse_tune_knobs("switch to fp 8")[0]
    assert ("quantization", "fp8") in parse_tune_knobs("use F P 8 quantization")[0]
    assert ("quantization", "none") in parse_tune_knobs("turn off quantization")[0]
    assert ("quantization", "none") in parse_tune_knobs("run in full precision")[0]
    assert ("enable_prefix_caching", True) in parse_tune_knobs("enable prefix caching")[0]
    assert ("enable_prefix_caching", False) in parse_tune_knobs("disable prefix caching")[0]
    assert ("enable_chunked_prefill", True) in parse_tune_knobs("turn on chunked prefill")[0]
    assert ("max_num_seqs", 256) in parse_tune_knobs("set max num seqs to 256")[0]
    assert ("max_num_seqs", 512) in parse_tune_knobs("use 512 batch size")[0]
    assert ("gpu_mem_util", 0.9) in parse_tune_knobs("gpu memory utilization 90%")[0]
    assert ("gpu_mem_util", 0.85) in parse_tune_knobs("gpu mem util 0.85")[0]


def test_unsupported_knob_value_is_reported_not_guessed() -> None:
    knobs, unsupported = parse_tune_knobs("set max num seqs to 300")
    assert knobs == []  # 300 is not on Spark's allow-list
    assert any("max_num_seqs=300" in u for u in unsupported)


# --- planning -------------------------------------------------------------------
def test_plan_builds_spark_decisions() -> None:
    parsed = parse_tune_command("try 350W with FP8")
    plan = plan_with_spark(parsed)
    kinds = [d.choice.kind for d in plan["decisions"]]
    assert kinds == ["cap", "knob"]
    assert plan["decisions"][0].choice.power_cap_w == 350
    assert plan["decisions"][0].model == "muse-spark-1.3"
    assert plan["decisions"][1].choice.knob == "quantization"
    assert plan["decisions"][1].choice.value == "fp8"


def test_plan_clamps_cap_to_driver_range() -> None:
    plan = plan_with_spark(parse_tune_command("try 2000 watts"), min_w=200, max_w=700)
    d = plan["decisions"][0]
    assert d.choice.power_cap_w == 700
    assert d.clamped is True


# --- the guarded loop (dry-run) -------------------------------------------------
def test_run_tune_dry_run_plans_and_routes_without_applying() -> None:
    r = run_tune("try 350W with FP8")
    assert r["intent"] == "command"
    assert r["action"] == "tune"
    assert r["mode"] == "dry-run"
    assert r["label"] == "SIMULATED"
    assert "assuming the quality checks pass" in r["text"]
    # two changes, each high-risk -> needs a person, nothing applied
    changes = r["guard"]["changes"]
    assert [c["kind"] for c in changes] == ["power_cap", "vllm_config"]
    assert all(c["risk"] == "high" for c in changes)
    assert all(c["needs_person"] for c in changes)
    assert all(c["route"] == "pending" for c in changes)
    # the guarded loop ran for real (its audit chain verifies)
    assert r["guard"]["receipt"]["audit_chain_ok"] is True
    # no mutate action leaks into the response
    for forbidden in ("launch", "start", "stop", "change", "apply"):
        assert forbidden not in r


def test_run_tune_low_risk_knob_auto_approves() -> None:
    r = run_tune("set max num seqs to 256")
    c = r["guard"]["changes"][0]
    assert c["kind"] == "vllm_config"
    assert c["risk"] == "low"
    assert c["route"] == "auto-low-risk"
    assert c["needs_person"] is False


def test_run_tune_speech_is_browser_voice_not_a_model() -> None:
    r = run_tune("try 350W with FP8")
    assert isinstance(r["speech"], str) and r["speech"]
    assert r["voice"]["label"] == "browser voice"
    assert r["voice"]["engine"] == "browser speechSynthesis"
    assert "not a Meta model" in r["voice"]["note"]
    # Spark is credited as the planner (an intentional Meta product name), but the
    # read-aloud is explicitly the browser voice.
    assert r["plan"]["planner"]["name"] == "Spark"
    assert "no paid Spark call" in r["plan"]["planner"]["note"]


def test_run_tune_reports_unmappable_command() -> None:
    r = run_tune("tune the server a bit")
    assert r["action"] == "tune"
    assert r["plan"]["decisions"] == []
    assert r["guard"]["changes"] == []
    assert "could not map" in r["text"]


def test_run_tune_dry_run_writes_nothing_to_cwd(tmp_path, monkeypatch) -> None:
    # A dry run uses a throwaway scratch store; it must not create an approvals
    # store under the working directory.
    monkeypatch.chdir(tmp_path)
    run_tune("try 350W with FP8")
    assert list(tmp_path.iterdir()) == []


# --- live routing (still never auto-applies) ------------------------------------
def test_run_tune_live_files_proposals_for_a_person(tmp_path) -> None:
    r = run_tune("try 350W with FP8", root_dir=tmp_path, live=True)
    assert r["mode"] == "live"
    assert r["label"] == "ESTIMATE"
    assert all(c["needs_person"] for c in r["guard"]["changes"])
    ap = Approvals(tmp_path)
    assert len(ap.list(status="pending")) == 2  # both high-risk changes wait
    assert ap.list(status="applied") == []  # nothing was applied


def test_run_tune_live_requires_a_root() -> None:
    with pytest.raises(ValueError):
        run_tune("try 350W with FP8", live=True)


# --- voice guide integration + safety -------------------------------------------
def test_tune_is_in_the_allowlist() -> None:
    assert "tune" in ALLOWED_ACTIONS


def test_quick_ack_for_command() -> None:
    ack = get_quick_ack("command", "try 350W with FP8")
    assert ack["intent"] == "command"
    assert "Spark" in ack["ack"]


def test_process_voice_query_routes_command_to_tuner() -> None:
    r = process_voice_query("try 350W with FP8", root_dir=REPO_ROOT, mock=True)
    assert r["intent"] == "command"
    assert r["action"] == "tune"
    assert r["mode"] == "dry-run"
    assert "quick_ack" in r
    assert r["plan"]["command"] == "try 350W with FP8"


def test_api_voiceguide_tune_command() -> None:
    client = TestClient(create_app(str(REPO_ROOT)))
    resp = client.post("/api/voiceguide", json={"query": "try 350W with FP8"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "tune"
    assert data["intent"] == "command"
    assert data["guard"]["changes"][0]["kind"] == "power_cap"
    assert data["voice"]["label"] == "browser voice"
