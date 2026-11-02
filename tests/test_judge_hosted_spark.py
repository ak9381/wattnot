"""Hosted Spark is the default judge; Llama answers only on 401/402/429s/spend stop."""
import json

import pytest

from wattnot.gates import judge as J
from wattnot.proof.baseten_planner import BasetenClient
from wattnot.spend.guard import Guard, Ledger, Price, TOKEN

ITEMS = [{"id": "a", "question": "1+1?"}, {"id": "b", "question": "2+2?"}]
BASE = {"a": "2", "b": "4"}
OK = {"model": "x", "usage": {"prompt_tokens": 100, "completion_tokens": 20},
      "choices": [{"message": {"content": json.dumps({"verdicts": [
          {"id": "a", "verdict": "same"}, {"id": "b", "verdict": "same"}]})}}]}


def _hosted(tmp_path, transport, monkeypatch, **gk):
    monkeypatch.delenv("BASETEN_METRICS_KEY", raising=False)
    monkeypatch.setenv("BASETEN_METRICS_KEY", "test-not-a-key")
    g = Guard("baseten", Price(TOKEN, 1.25, 4.25), Ledger(tmp_path / "l.jsonl"),
              sleep=lambda s: None, **gk)
    c = BasetenClient(model=J.SPARK_HOSTED_JUDGE, cap_usd=98, transport=transport)
    return J.HostedSparkJudge(client=c, guard=g), g


def _http(code):
    from wattnot.proof.baseten_planner import BasetenError

    def t(req):
        raise BasetenError(f"HTTP {code}: nope")
    return t


def _run(primary):
    llama = lambda body: OK  # noqa: E731
    fb = J.FallbackJudge(primary, llama, J.SPARK_HOSTED_JUDGE, J.LLAMA_JUDGE)
    return J.judge(BASE, BASE, ITEMS, transport=fb, model=J.SPARK_HOSTED_JUDGE)


def test_default_is_hosted_spark():
    assert J.MODEL == J.SPARK_HOSTED_JUDGE or J.MODEL.startswith("meta-")


def test_hosted_spark_answers_and_is_recorded(tmp_path, monkeypatch):
    seen = {}

    def t(req):
        seen.update(req["body"])
        return OK
    h, g = _hosted(tmp_path, t, monkeypatch)
    r = _run(h)
    assert r.passed and r.detail["judge_model"] == J.SPARK_HOSTED_JUDGE
    assert r.detail["judge_path"] == "hosted-spark"
    assert seen["reasoning_effort"] == "low" and seen["temperature"] == 0
    assert 0 < g.committed() < 0.01


@pytest.mark.parametrize("code", [401, 402])
def test_auth_or_credit_falls_back_to_llama(tmp_path, monkeypatch, code):
    h, g = _hosted(tmp_path, _http(code), monkeypatch)
    r = _run(h)
    assert r.passed and r.detail["judge_model"] == J.LLAMA_JUDGE
    assert r.detail["judge_path"] == "llama-fallback"
    assert ("HardStop" if code == 402 else "SoftStop") in r.detail["fallback_reason"]


def test_repeated_429_falls_back(tmp_path, monkeypatch):
    calls = []

    def t(req):
        calls.append(1)
        return _http(429)(req)
    h, g = _hosted(tmp_path, t, monkeypatch, max_429=3)
    r = _run(h)
    assert len(calls) == 4 and r.detail["judge_path"] == "llama-fallback"


def test_spend_stop_falls_back_without_calling(tmp_path, monkeypatch):
    calls = []
    h, g = _hosted(tmp_path, lambda req: calls.append(1) or OK, monkeypatch)
    g.mark_stop("hard", "test")
    r = _run(h)
    assert not calls and r.detail["judge_path"] == "llama-fallback"


def test_no_key_falls_back_and_reserves_nothing(tmp_path, monkeypatch):
    h, g = _hosted(tmp_path, lambda req: OK, monkeypatch)
    monkeypatch.delenv("BASETEN_METRICS_KEY")
    r = _run(h)
    assert r.detail["judge_path"] == "llama-fallback" and g.committed() == 0


def test_round_and_smoke_caps_lower_the_judge_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("WATTNOT_SPEND_DIR", str(tmp_path))
    monkeypatch.setenv("BASETEN_METRICS_KEY", "test-not-a-key")
    # no round cap set: the judge keeps its own default
    monkeypatch.delenv(J.BASETEN_CAP_ENV, raising=False)
    assert J.baseten_cap_usd() == J.JUDGE_BASETEN_CAP_USD
    assert J.HostedSparkJudge().guard.hard_usd == J.JUDGE_BASETEN_CAP_USD
    # the $8 per-round hard stop is lower, so it wins — for the guard AND the client
    monkeypatch.setenv(J.BASETEN_CAP_ENV, "8.0")
    assert J.baseten_cap_usd() == 8.0
    h = J.HostedSparkJudge()
    assert h.cap_usd == 8.0 and h.guard.hard_usd == 8.0 and h.client.cap_usd == 8.0
    # a $1 smoke cap is lower still
    monkeypatch.setenv(J.BASETEN_CAP_ENV, "1.0")
    assert J.baseten_cap_usd() == 1.0 and J.HostedSparkJudge().guard.hard_usd == 1.0
    # a cap above the judge default can never raise it
    monkeypatch.setenv(J.BASETEN_CAP_ENV, "500")
    assert J.baseten_cap_usd() == J.JUDGE_BASETEN_CAP_USD


def test_judge_refused_once_the_round_cap_is_spent(tmp_path, monkeypatch):
    # Judge spend near the $8 round cap: the next judge call is refused (a labelled spend stop)
    # before any hosted call, and the gate falls back to the local Llama rather than overspend.
    monkeypatch.setenv("BASETEN_METRICS_KEY", "test-not-a-key")
    monkeypatch.setenv(J.BASETEN_CAP_ENV, "8.0")
    cap = J.baseten_cap_usd()
    g = Guard("baseten", Price(TOKEN, 1.25, 4.25), Ledger(tmp_path / "l.jsonl"),
              sleep=lambda s: None, hard_usd=cap, soft_usd=min(90.0, cap),
              lag_margin_usd=min(2.0, cap / 4))
    g.ledger.append({"kind": "reserve", "provider": "baseten", "rid": "prior-judge",
                     "usd": 7.9, "item": "judge"})   # $7.9 of the $8 round cap already used
    assert g.committed() == pytest.approx(7.9)
    calls = []
    c = BasetenClient(model=J.SPARK_HOSTED_JUDGE, cap_usd=cap,
                      transport=lambda req: calls.append(1) or OK)
    h = J.HostedSparkJudge(client=c, guard=g)
    r = _run(h)
    assert not calls  # the hosted judge was refused before making a call
    assert r.detail["judge_path"] == "llama-fallback"
    assert "baseten" in r.detail["fallback_reason"].lower()


def test_health_uses_the_one_baseten_key_lookup():
    from wattnot import health
    ep = next(e for e in health.ENDPOINTS if e["id"] == "baseten")
    assert health._key(ep, {"BASETEN_METRICS_KEY": "a", "BASETEN_METRICS_KEY": "b"}) == "b"
    assert health._key(ep, {"BASETEN_METRICS_KEY": "b"}) == "b"
    assert health._key(ep, {}) is None
