"""Spark per-answer grading: MOCK only, no network, no real ledger."""
import json

from wattnot.longrun import spark_grade as sg
from wattnot.longrun.grade import grade_one
from wattnot.longrun.tasks import build
from wattnot.meta.client import GuardedClient
from wattnot.meta.ledger import SpendLedger


def _opener(verdict):
    def op(req, timeout):
        out = {"choices": [{"message": {"content": json.dumps({"correct": verdict, "reason": "r"})}}],
               "usage": {"prompt_tokens": 300, "completion_tokens": 30}}
        return 200, json.dumps(out).encode()
    return op


def _grader(tmp_path, verdict, cap=120.0):
    led = SpendLedger(tmp_path / "l.json", cap_usd=cap, soft_usd=min(cap, 100.0))
    return sg.SparkGrader(mock=False, client=GuardedClient(opener=_opener(verdict), key="k", ledger=led),
                          round_id="r01", arm="with_wattnot")


def test_mock_agrees_and_is_labelled(tmp_path):
    g = sg.SparkGrader(mock=True, round_id="r01")
    assert g("a", "gsm8k", "p", "x", True)["spark"] is True
    assert g("b", "gsm8k", "p", "x", False)["spark"] is False
    s = g.summary()
    assert s["label"] == "MOCK" and s["agreement"] == 1.0 and s["spark_graded"] == 2


def test_deterministic_decides_on_disagreement(tmp_path):
    g = _grader(tmp_path, verdict=False)
    rec = g("x", "gsm8k", "p", "ans", True)
    assert rec["deterministic"] is True and rec["spark"] is False and rec["disagree"]
    assert g.summary()["disagreements"] == 1


def test_cap_stops_grading_for_rest_of_run(tmp_path):
    g = _grader(tmp_path, verdict=True, cap=0.0001)
    r1, r2 = g("a", "gsm8k", "p", "x", True), g("b", "gsm8k", "p", "x", True)
    assert r1["spark"] is None and "cap" in r1["spark_reason"].lower()
    assert g.stopped and r2["spark"] is None and r2["spark_reason"].startswith("not graded")


def test_off_without_round_env(monkeypatch):
    monkeypatch.delenv(sg.ROUND_ENV, raising=False)
    assert sg.grader_from_env() is None


def test_on_with_round_env(monkeypatch):
    monkeypatch.setenv(sg.ROUND_ENV, "r03")
    monkeypatch.setenv(sg.ARM_ENV, "with_wattnot")
    monkeypatch.setenv("WATTNOT_META_MOCK", "1")
    g = sg.grader_from_env()
    assert g is not None and g.round_id == "r03" and g.mock


def test_off_when_config_disables(tmp_path, monkeypatch):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"spark_grade_all": False}))
    monkeypatch.setenv(sg.ROUND_ENV, "r01")
    assert sg.grader_from_env(p) is None


def test_grade_one_keeps_ok_deterministic(tmp_path):
    from wattnot.longrun.harness import FakeChat
    from wattnot.proof.fake import FakeNvml
    tasks = build()[:6]
    nv = FakeNvml()
    chat = FakeChat(nv, nv.device_handle(0), tasks)
    g = _grader(tmp_path, verdict=False)   # Spark says wrong to everything
    for t in tasks:
        base = grade_one(t, chat)
        got = grade_one(t, chat, g)
        assert got.ok == base.ok
        assert "spark" in got.detail


def test_matrix_round_dry(tmp_path):
    from wattnot.longrun import matrix_round as mr
    d = mr.dry_round("r01", tmp_path)
    assert d["label"] == "MOCK" and (tmp_path / "r01.json").exists()
    assert d["spark_summary"]["answers"] == 384
    assert d["results"]["accuracy_without"] >= d["results"]["accuracy_with"]


def test_estimate_under_cap():
    from wattnot.longrun import matrix_round as mr
    e = mr.estimate()
    assert e["label"] == "ESTIMATE" and e["worst_case_usd_total"] < e["cap_usd"]
