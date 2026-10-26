"""T21: Muse Spark judge on the recorded fixture (no network)."""

from __future__ import annotations

import json
from pathlib import Path

from wattnot.gates.judge import agreement, judge, load_judge_set, parse_verdicts, request_body

REC = json.loads((Path(__file__).parent / "fixtures" / "judge_recorded.json").read_text())
ITEMS = load_judge_set()


def _outs():
    return ({i["id"]: i["baseline"] for i in ITEMS}, {i["id"]: i["candidate"] for i in ITEMS})


def test_set_has_30_public_items_with_labels():
    assert len(ITEMS) == 30
    assert {i["label"] for i in ITEMS} == {"same", "worse"}
    assert sum(i["label"] == "worse" for i in ITEMS) == 10


def test_one_call_for_all_items():
    calls = []

    def t(body):
        calls.append(body)
        return REC

    judge(*_outs(), ITEMS, transport=t)
    assert len(calls) == 1
    sent = json.loads(calls[0]["messages"][1]["content"])
    assert len(sent) == 30 and set(sent[0]) == {"id", "question", "baseline", "candidate"}


def test_recorded_agreement():
    a = agreement(ITEMS, parse_verdicts(REC, [i["id"] for i in ITEMS]))
    assert a["n"] == 30 and a["agreement"] >= 0.9


def test_gate_fails_when_a_third_is_worse():
    g = judge(*_outs(), ITEMS, transport=lambda b: REC)
    assert g.gate == "judge" and not g.passed and g.detail["worse"] >= 9


def test_gate_passes_on_same_answers():
    same = [i for i in ITEMS if i["label"] == "same"]
    rec = {"model": "muse-spark-1.3", "usage": {}, "choices": [{"message": {"content": json.dumps(
        {"verdicts": [{"id": i["id"], "verdict": "same"} for i in same]})}}]}
    assert judge(*_outs(), same, transport=lambda b: rec).passed


def test_fails_closed_on_bad_response():
    g = judge(*_outs(), ITEMS, transport=lambda b: {"choices": [{"message": {"content": "nope"}}]})
    assert not g.passed and "unavailable" in g.reason
    g = judge(*_outs(), ITEMS, transport=lambda b: (_ for _ in ()).throw(OSError("down")))
    assert not g.passed


def test_request_is_strict_json_schema():
    body = request_body(ITEMS[:2])
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["reasoning_effort"] == "minimal"
