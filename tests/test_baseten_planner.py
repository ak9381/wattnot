"""The Baseten open-weights planner: client, spend guard, planner. No network."""
from __future__ import annotations

import json

import pytest

from wattnot.meta.agent import _body, usage_of
from wattnot.proof.baseten_planner import (
    MODEL,
    BasetenClient,
    BasetenError,
    CapReached,
    OpenWeightsPlanner,
    price_usd,
)

KEY = "sk-test-SECRET-xyz-987654321"


def _resp(prompt=410, completion=38, cached=0):
    return {"model": MODEL,
            "choices": [{"message": {"content": json.dumps(
                {"kind": "try", "cap_w": 425, "reason": "500 W held p99"})}}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                      "prompt_tokens_details": {"cached_tokens": cached}}}


def _client(tmp_path=None, transport=None, **kw):
    seen = []

    def fake(req):
        seen.append(req)
        if transport is not None:
            return transport(req)
        return _resp()

    kw.setdefault("ledger_path", tmp_path / "baseten-ledger.json" if tmp_path else None)
    c = BasetenClient(transport=fake, **kw)
    c.seen = seen
    return c


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("BASETEN_API_KEY", KEY)


def test_posts_to_chat_completions_with_the_forced_model(key, tmp_path):
    c = _client(tmp_path)
    c({"messages": []}, purpose="next-power-cap")
    req = c.seen[0]
    assert req["url"] == "https://inference.baseten.co/v1/chat/completions"
    assert req["body"]["model"] == MODEL
    assert req["purpose"] == "next-power-cap"
    assert "sk-test" not in json.dumps(req)  # the injected transport never sees the key


def test_default_max_tokens_is_at_least_4000(key, tmp_path):
    c = _client(tmp_path)
    c({"messages": []})
    assert c.seen[0]["body"]["max_tokens"] >= 4000
    c({"messages": [], "max_tokens": 100})  # an explicit cap is left alone
    assert c.seen[1]["body"]["max_tokens"] == 100


def test_another_model_is_refused(key, tmp_path):
    c = _client(tmp_path)
    with pytest.raises(BasetenError, match="refusing to send model"):
        c({"model": "muse-spark-1.3", "messages": []})
    assert c.seen == [] and c.entries == []


def test_missing_key_is_refused_before_anything_moves(tmp_path, monkeypatch):
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    monkeypatch.delenv("BASETEN_METRICS_KEY", raising=False)
    c = _client(tmp_path)
    with pytest.raises(BasetenError, match="is set"):
        c({"messages": []})
    assert c.seen == []


def test_key_appears_nowhere(key, tmp_path):
    c = _client(tmp_path)
    c({"messages": []})
    blob = json.dumps(c.entries) + repr(c)
    if c.ledger_path is not None:
        blob += c.ledger_path.read_text()
    assert KEY not in blob
    assert KEY not in repr(c)
    try:
        BasetenClient(transport=lambda req: (_ for _ in ()).throw(OSError("down")),
                       ledger_path=None)(_body("s", {}, {}, "N", effort="low",
                                              max_tokens=4000))
    except BasetenError as e:
        assert KEY not in str(e)
    else:  # pragma: no cover - the transport always raises
        raise AssertionError("no error raised")


def test_ledger_row_shape_and_pricing(key, tmp_path):
    c = _client(tmp_path)
    c({"messages": []}, purpose="next-power-cap")
    doc = json.loads((tmp_path / "baseten-ledger.json").read_text())
    assert doc["provider"] == "baseten"
    (row,) = doc["entries"]
    assert row == {"ts": row["ts"], "model": MODEL, "purpose": "next-power-cap",
                   "input_tokens": 410, "cached_tokens": 0, "output_tokens": 38,
                   "usd": price_usd(410, 38), "label": "MEASURED", "provider": "baseten"}
    assert len(row["ts"]) >= 20 and KEY not in json.dumps(doc)
    assert c.total_usd() == pytest.approx(price_usd(410, 38))


def test_cached_tokens_are_priced_at_the_cached_rate(key, tmp_path):
    c = _client(tmp_path, transport=lambda req: _resp(1000, 100, 800))
    c({"messages": []})
    (row,) = c.entries
    assert (row["input_tokens"], row["cached_tokens"], row["output_tokens"]) == (1000, 800, 100)
    assert row["usd"] == pytest.approx(price_usd(1000, 100, 800))
    assert row["usd"] < price_usd(1000, 100)  # the cache discount applied


def test_cap_refusal_spends_nothing(key, tmp_path):
    c = _client(tmp_path, cap_usd=0.00001)
    with pytest.raises(CapReached, match="cap"):
        c({"messages": []})
    assert c.seen == [] and c.entries == []
    assert KEY not in str(c.total_usd())


def test_spend_then_refuse(key, tmp_path):
    c = _client(tmp_path, cap_usd=0.0175)
    c({"messages": []})
    assert len(c.seen) == 1 and c.total_usd() > 0
    with pytest.raises(CapReached):
        c({"messages": []})
    assert len(c.seen) == 1 and len(c.entries) == 1


def test_reply_fits_what_the_planner_reads(key, tmp_path):
    c = _client(tmp_path)
    raw = c({"messages": []})
    assert usage_of(raw)["input"] == 410


# --- the planner ---------------------------------------------------------------


def _fake_client(answers):
    bodies = []

    def call(body, purpose=""):
        bodies.append(body)
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return {"choices": [{"message": {"content": json.dumps(a)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    call.bodies = bodies
    return call


def test_open_weights_planner_proposes_under_its_own_name():
    p = OpenWeightsPlanner(_fake_client(
        [{"kind": "try", "cap_w": 425, "reason": "500 W held p99"}]))
    assert p.name == "muse-spark-open" and p.partner == "baseten"
    prop = p.propose([])
    assert prop["by"] == "muse-spark-open" and prop["cap_w"] == 425
    assert prop["model"] == MODEL
    assert prop["usd"] == pytest.approx(price_usd(10, 5), abs=1e-6)
    assert prop["usage"] == {"input": 10, "output": 5, "reasoning": 0}


def test_open_weights_planner_sends_the_baseten_model():
    client = _fake_client([{"kind": "stop", "cap_w": None, "reason": "done"}])
    OpenWeightsPlanner(client).propose([])
    assert client.bodies[0]["model"] == MODEL


def test_open_weights_planner_falls_back_like_muse():
    p = OpenWeightsPlanner(_fake_client(
        [{"kind": "try", "cap_w": 421, "reason": "off list"}]))
    prop = p.propose([])
    assert prop["by"] == "bisect-fallback" and "off the allow-list" in prop["muse_error"]
    p = OpenWeightsPlanner(_fake_client([RuntimeError("down")]))
    prop = p.propose([])
    assert prop["by"] == "bisect-fallback" and prop["proposal_accepted"] is None
