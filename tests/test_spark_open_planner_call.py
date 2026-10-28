"""The Muse Spark Open planner call on Baseten, with the network mocked out.

Checks that a good answer drives the cap and lands in the receipt trace with
provider, model id, prompt hash and latency, and that a failed call falls back
to the bisect rule and says so."""

import json
import logging

import pytest

from wattnot.proof.baseten_planner import BasetenClient, OpenWeightsPlanner
from wattnot.receipt.build import planner_trace_from_events

HIST = [{"cap_w": 600, "accepted": True, "why_not": None, "energy_change_pct": -8.0,
         "p99_change_pct": 2.0, "quality_pass_pct": 93.7, "stock_pass_pct": 93.7}]


def _reply(obj):
    return {"choices": [{"message": {"content": json.dumps(obj)}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 300}}


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    monkeypatch.setenv("BASETEN_API_KEY", "test-not-a-real-key")


def _planner(transport):
    return OpenWeightsPlanner(BasetenClient(transport=transport, cap_usd=1.0))


def _event(prop):
    return {"type": "decision", "data": {
        "choice": {"kind": "cap", "power_cap_w": prop.get("cap_w")},
        "reason": prop["reason"], "model": prop.get("model") if prop["by"] != "bisect-fallback"
        else "bisect-fallback", "call": prop.get("call")}}


def test_spark_open_answer_sets_the_cap_and_is_recorded():
    sent = []

    def transport(req):
        sent.append(req)
        return _reply({"kind": "try", "cap_w": 500, "reason": "halve the gap"})

    p = _planner(transport)
    prop = p.propose(HIST)
    assert prop["by"] == "muse-spark-open" and prop["cap_w"] == 500
    call = prop["call"]
    assert call["provider"] == "baseten" and call["model"] == p.model
    assert len(call["prompt_sha256"]) == 64 and call["latency_ms"] is not None
    assert call["fallback"] is False and call["label"] == "MEASURED"
    assert "test-not-a-real-key" not in json.dumps(sent[0]["body"])
    row = planner_trace_from_events([_event(prop)])[0]
    assert row["source"] == "spark" and row["call"]["prompt_sha256"] == call["prompt_sha256"]


def test_same_history_gives_same_prompt_hash():
    t = lambda req: _reply({"kind": "try", "cap_w": 500, "reason": "x"})  # noqa: E731
    assert _planner(t).propose(HIST)["call"]["prompt_sha256"] == \
        _planner(t).propose(HIST)["call"]["prompt_sha256"]


def test_failed_call_falls_back_to_bisect_and_logs_it(caplog):
    def transport(req):
        raise TimeoutError

    with caplog.at_level(logging.WARNING, logger="wattnot.planner.spark_open"):
        prop = _planner(transport).propose(HIST)
    assert prop["by"] == "bisect-fallback" and prop["kind"] == "try"
    assert prop["call"]["fallback"] is True and "call failed" in prop["call"]["fallback_reason"]
    assert any("FALLBACK" in r.message for r in caplog.records)
    assert planner_trace_from_events([_event(prop)])[0]["source"] == "fallback"


def test_off_list_cap_is_rejected_and_falls_back():
    prop = _planner(lambda req: _reply({"kind": "try", "cap_w": 123, "reason": "x"})).propose(HIST)
    assert prop["by"] == "bisect-fallback" and prop["proposal_accepted"] is False
    assert "answer rejected" in prop["call"]["fallback_reason"]
