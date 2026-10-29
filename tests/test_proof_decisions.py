"""Muse Spark is on the proof's critical path, and its choices are logged.

Covers the decisions.jsonl trail: valid proposals, off-list / malformed /
failed Muse answers falling back to bisect, explanation length limits, schema
validity of every line, the deterministic explanation text, and the summary
the run report prints. All Muse calls are fakes; no network.
"""

from __future__ import annotations

import json

import pytest

from wattnot import schemas
from wattnot.proof.decisions import (
    append_record,
    explain_decision,
    format_decisions_summary,
    load_records,
    new_record,
    settle_record,
    summarize_decisions,
)
from wattnot.proof.planner import (
    EXPLANATION_MAX,
    MODEL,
    SCHEMA,
    BisectPlanner,
    MusePlanner,
    build_prompt,
    call_usd,
    clean_explanation,
)
from wattnot.proof.run import run_session, write_report


class FakeMeta:
    """Replays canned Muse answers and keeps the request bodies."""

    def __init__(self, answers):
        self.answers, self.bodies = list(answers), []

    def __call__(self, body, purpose=""):
        self.bodies.append(body)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return {"choices": [{"message": {"content": json.dumps(answer)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def _proposal(**over):
    base = {"kind": "try", "cap_w": 400, "reason": "450 W kept p99 within 4%",
            "by": "muse-spark", "proposal_accepted": True, "explanation": None,
            "usage": {"input": 10, "output": 5, "reasoning": 0},
            "model": MODEL, "usd": call_usd(MODEL, {"input": 10, "output": 5})}
    base.update(over)
    return base


def _summary(**over):
    base = {"accepted": True, "p99_change_pct": {"mean": 3.0},
            "energy_change_pct": {"mean": -12.0}, "quality": {"cap_pct": 98.0},
            "why_not": []}
    base.update(over)
    return base


_UNSET = object()


def _settled(proposal=None, summary=_UNSET):
    """A settled muse-spark record for 400 W; `summary=None` means kind=stop."""
    record = new_record(1, allowed=[400, 425], proposal=_proposal(**(proposal or {})))
    if summary is _UNSET:
        summary = {}
    return settle_record(record, _summary(**summary) if summary is not None else None)


# --- planner answers ----------------------------------------------------------


def test_valid_proposal_carries_usage_usd_and_explanation():
    muse = MusePlanner(FakeMeta([{"kind": "try", "cap_w": 425,
                                  "reason": "500 W held p99",
                                  "explanation": "Trying 425 W next since 500 W held p99."}]))
    prop = muse.propose([])
    assert prop["cap_w"] == 425 and prop["by"] == "muse-spark"
    assert prop["proposal_accepted"] is True
    assert prop["explanation"] == "Trying 425 W next since 500 W held p99."
    assert prop["usage"] == {"input": 10, "output": 5, "reasoning": 0}
    assert prop["usd"] == call_usd(MODEL, prop["usage"]) > 0


def test_off_list_proposal_falls_back_and_is_logged():
    muse = MusePlanner(FakeMeta([{"kind": "try", "cap_w": 421, "reason": "off list"}]))
    prop = muse.propose([])
    assert prop["by"] == "bisect-fallback" and prop["proposal_accepted"] is False
    assert "off the allow-list" in prop["muse_error"]
    assert prop["usage"] is None and prop["usd"] is None
    record = new_record(1, allowed=[400, 425], proposal=prop)
    assert record["planner"] == "bisect" and record["fallback"] is True
    assert "off the allow-list" in (record["fallback_reason"] or "")


def test_api_error_falls_back_with_no_answer_to_validate():
    muse = MusePlanner(FakeMeta([RuntimeError("down")]))
    prop = muse.propose([])
    assert prop["by"] == "bisect-fallback" and prop["proposal_accepted"] is None
    assert "RuntimeError" in prop["muse_error"]


def test_malformed_json_falls_back_as_rejected():
    bad = FakeMeta([])

    def reply(body, purpose=""):
        bad.bodies.append(body)
        return {"choices": [{"message": {"content": "{not json"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    prop = MusePlanner(reply).propose([])
    assert prop["by"] == "bisect-fallback" and prop["proposal_accepted"] is False


def test_empty_answer_falls_back_as_rejected():
    def reply(body, purpose=""):
        return {"choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    prop = MusePlanner(reply).propose([])
    assert prop["by"] == "bisect-fallback" and prop["proposal_accepted"] is False
    assert "empty answer" in prop["muse_error"]


def test_long_explanation_is_truncated_not_dropped():
    long = "x" * (EXPLANATION_MAX + 50)
    assert clean_explanation(long) == "x" * EXPLANATION_MAX
    assert clean_explanation("   ") is None
    muse = MusePlanner(FakeMeta([{"kind": "try", "cap_w": 425, "reason": "r",
                                  "explanation": long}]))
    prop = muse.propose([])
    assert prop["explanation"] == "x" * EXPLANATION_MAX


# --- the prompt ----------------------------------------------------------------


def test_prompt_states_gates_allowed_and_trials_only():
    muse = MusePlanner(FakeMeta([{"kind": "try", "cap_w": 425, "reason": "r",
                                  "explanation": "e"}]))
    hist = [{"cap_w": 500, "accepted": True, "why_not": [], "energy_change_pct": -10.0,
             "p99_change_pct": 3.0, "quality_pass_pct": 100.0, "stock_pass_pct": 100.0}]
    muse.propose(hist)
    body = muse.client.bodies[0]
    payload = json.loads(body["messages"][1]["content"])
    assert set(payload) == {"range_w", "default_w", "allowed", "trials", "prior"}
    assert set(payload["trials"][0]) == {"cap_w", "energy_change_pct", "p99_change_pct",
                                         "quality_pass_pct", "stock_pass_pct",
                                         "accepted", "why_not"}
    system = body["messages"][0]["content"]
    assert "at most 1 point" in system and "at most 10%" in system
    assert "below zero" in system and "explanation" in system


def test_build_prompt_matches_what_is_sent():
    system, payload = build_prompt([], 300, 700)
    assert payload["allowed"][0] == 300 and 700 not in payload["allowed"]
    assert SCHEMA["properties"]["explanation"]["maxLength"] == EXPLANATION_MAX


# --- deterministic explanations --------------------------------------------------


def test_explanation_for_accepted_cap():
    assert explain_decision(_settled()) == (
        "Muse Spark proposed 400 W: 450 W kept p99 within 4%. "
        "The gate ACCEPTED it (MEASURED): p99 +3.0%, energy -12.0% per request, "
        "quality 98.0% pass.")


def test_explanation_for_rejected_cap():
    record = _settled(summary={"accepted": False, "p99_change_pct": {"mean": 12.0},
                               "energy_change_pct": {"mean": -5.0},
                               "quality": {"cap_pct": 99.0},
                               "why_not": ["p99 up 12.0% (limit 10%)"]})
    assert explain_decision(record) == (
        "Muse Spark proposed 400 W: 450 W kept p99 within 4%. "
        "The gate REJECTED it (MEASURED): p99 up 12.0% (limit 10%); p99 +12.0%, "
        "energy -5.0% per request, quality 99.0% pass.")


def test_explanation_for_fallback():
    record = _settled(proposal={"by": "bisect-fallback", "proposal_accepted": False,
                                "reason": "bisect fallback", "cap_w": 425,
                                "muse_error": "ValueError: off the allow-list: 421",
                                "usage": None, "usd": None},
                      summary={"p99_change_pct": {"mean": 2.0},
                               "energy_change_pct": {"mean": -8.0},
                               "quality": {"cap_pct": 100.0}})
    assert explain_decision(record) == (
        "Muse Spark's answer was rejected (ValueError: off the allow-list: 421), "
        "so the bisect rule picked 425 W instead: bisect fallback. "
        "The gate ACCEPTED it (MEASURED): p99 +2.0%, energy -8.0% per request, "
        "quality 100.0% pass.")


def test_explanation_for_stop_and_pending():
    stop = new_record(3, allowed=[400], proposal={"kind": "stop", "cap_w": None,
                                                 "reason": "done", "by": "muse-spark",
                                                 "proposal_accepted": True})
    assert explain_decision(settle_record(stop, None)) == (
        "Muse Spark proposed stopping: done. No more caps were tried.")
    pending = new_record(1, allowed=[400], proposal=_proposal())
    assert pending["gate"] == "pending"
    assert explain_decision(pending).endswith("The gate has not ruled yet.")


# --- summary ---------------------------------------------------------------------


def test_summary_counts_planners_rates_and_fallbacks():
    records = [
        _settled(),
        _settled(proposal={"by": "bisect-fallback", "proposal_accepted": False,
                            "reason": "bisect fallback", "cap_w": 425,
                            "muse_error": "ValueError: off the allow-list",
                            "usage": None, "usd": None}),
        _settled(summary={"accepted": False,
                          "why_not": ["p99 up 12.0% (limit 10%)"]}),
    ]
    summary = summarize_decisions(records)
    assert summary["total"] == 3
    assert summary["by_planner"] == {"muse-spark": 2, "bisect": 1}
    assert summary["gate_acceptance_rate"] == pytest.approx(2 / 3)
    assert summary["fallbacks"] == 1
    text = format_decisions_summary(summary)
    assert "muse-spark 2" in text and "bisect 1" in text and "Fallbacks to bisect: 1" in text


def test_summary_empty_is_still_printable():
    summary = summarize_decisions([])
    assert summary["total"] == 0 and summary["fallbacks"] == 0
    assert summary["gate_acceptance_rate"] is None
    assert "0 planner proposals" in format_decisions_summary(summary)


# --- jsonl trail -------------------------------------------------------------------


def test_every_jsonl_line_validates(tmp_path):
    out = tmp_path / "run"
    for proposal in (_proposal(),
                     _proposal(by="bisect-fallback", proposal_accepted=False,
                               reason="bisect fallback", cap_w=425,
                               muse_error="ValueError: off the allow-list: 421",
                               usage=None, usd=None)):
        record = new_record(1, allowed=[400, 425], proposal=proposal)
        append_record(out, settle_record(record, _summary()))
    lines = (out / "decisions.jsonl").read_text().splitlines()
    assert len(lines) == 2
    for line in lines:
        schemas.validate_decision(json.loads(line))
    assert [r["planner"] for r in load_records(out)] == ["muse-spark", "bisect"]


def test_trail_is_append_only(tmp_path):
    out = tmp_path / "run"
    record = settle_record(new_record(1, allowed=[400], proposal=_proposal()), _summary())
    append_record(out, record)
    first = (out / "decisions.jsonl").read_text()
    append_record(out, settle_record(
        new_record(2, allowed=[425], proposal=_proposal(cap_w=425)), _summary()))
    assert (out / "decisions.jsonl").read_text().startswith(first)
    assert len(load_records(out)) == 2


def test_no_secrets_or_outputs_in_the_trail(tmp_path):
    out = tmp_path / "run"
    append_record(out, _settled())
    blob = (out / "decisions.jsonl").read_text()
    for banned in ("META_API_KEY", "Bearer", "api_key"):
        assert banned not in blob
    assert set(json.loads(blob)) <= set(schemas.load_schema("decision")["properties"])


# --- run wiring ----------------------------------------------------------------------


def _fake_step(cap_w):
    return {"cap_w": cap_w, "accepted": True, "why_not": [],
            "energy_change_pct": {"mean": -10.0, "sd": 1.0},
            "p99_change_pct": {"mean": 2.0, "sd": 0.5},
            "quality": {"cap_pct": 100.0, "stock_pct": 100.0}}


def test_run_session_writes_decisions_and_report_prints_them(tmp_path):
    muse = MusePlanner(FakeMeta([{"kind": "try", "cap_w": 450, "reason": "first probe",
                                  "explanation": "Probing 450 W."},
                                 {"kind": "stop", "cap_w": None, "reason": "enough",
                                  "explanation": "Enough evidence."}]))
    steps = run_session(_fake_step, muse, (600,), lambda k, d: None,
                        max_proposals=4, out=tmp_path)
    assert [s["cap_w"] for s in steps] == [450, 600]
    records = load_records(tmp_path)
    assert [(r["step"], r["planner"], r["gate"]) for r in records] == [
        (1, "muse-spark", "accepted"), (2, "muse-spark", "stop")]
    assert records[0]["proposed_cap_w"] == 450
    assert records[0]["muse_explanation"] == "Probing 450 W."
    assert records[0]["usd"] and records[0]["usage"]["input"] == 10
    res = write_report(tmp_path, "m", None, steps, {})
    assert res["decisions_summary"]["total"] == 2
    text = (tmp_path / "results.md").read_text()
    assert "Planner decisions:" in text and "Muse Spark proposed 450 W" in text


def test_run_session_without_out_writes_no_trail(tmp_path):
    steps = run_session(_fake_step, BisectPlanner(), (600,), lambda k, d: None,
                        max_proposals=1)
    assert steps and not (tmp_path / "decisions.jsonl").exists()
    res = write_report(tmp_path, "m", None, steps, {})
    assert res["decisions_summary"]["total"] == 0
