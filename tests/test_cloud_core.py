"""Cloud mode building blocks: prices, effort knobs, prompt layout, transport, energy."""

import io
import json
import urllib.error

import pytest

from wattnot.cloud import BASE_URL, MODEL, efforts, energy, prompts
from wattnot.cloud import transport as tr
from wattnot.cloud.pricing import Rates, cost_usd

KEY = "abc123.SECRETSECRETSECRET"


def test_model_slug_and_base_url_are_exact():
    assert MODEL == "meta-models/muse-spark-open-1026"
    assert BASE_URL == "https://inference.baseten.co/v1"


def test_cost_uses_cached_discount_and_bills_reasoning_as_output():
    r = Rates()
    # 1M uncached in + 1M cached in + 1M out
    assert cost_usd(r, 2_000_000, 1_000_000, 1_000_000) == pytest.approx(1.25 + 0.15 + 4.25)
    assert cost_usd(r, 100, 500, 0) == cost_usd(r, 100, 100, 0)  # cached cannot exceed prompt
    assert r.confirmed is False


def test_max_tokens_scale_with_effort_and_stop_is_capped():
    a = [efforts.max_tokens_for(e, 100) for e in efforts.EFFORTS]
    assert a == sorted(a) and a[0] < a[-1]
    assert efforts.max_tokens_for("xhigh", 10**6) == efforts.MAX_TOKENS_CEILING
    assert len(efforts.stop_for("low", ["a", "b", "c", "d", "e"])) == 4
    assert efforts.stop_for("xhigh") == []  # never cut the reference short
    with pytest.raises(ValueError):
        efforts.max_tokens_for("turbo", 10)


def test_calibration_uses_p95_plus_headroom():
    got = efforts.calibrated_allowance({"low": [400] * 20}, answer_budget=100)
    assert got["low"] == 420 and got["high"] == efforts.DEFAULT_ALLOWANCE["high"]


def test_parse_orders_drops_junk_and_fills_gaps():
    got = efforts.parse_orders('noise {"orders":{"a":["high","bogus","high"]}} tail', ["a", "b"])
    assert got["a"] == ["high", "low", "medium"]
    assert got["b"] == ["low", "medium", "high"]  # never xhigh: that is the reference
    for bad in ("no json", '{"orders": 3}'):
        with pytest.raises(ValueError):
            efforts.parse_orders(bad, ["a"])


def test_stable_parts_go_first_and_volatile_values_are_caught():
    m = prompts.build_messages(system="S", tools="T", shared=["doc"], task="Q 1")
    assert m[0]["content"] == "S\n\nT\n\ndoc" and m[-1] == {"role": "user", "content": "Q 1"}
    assert prompts.volatile_in_prefix(m) == []
    bad = prompts.build_messages(system="now 2026-10-27 09:00:01 req id: 5", task="Q")
    assert set(prompts.volatile_in_prefix(bad)) == {"timestamp", "request id"}
    assert prompts.prefix_hash(m) == prompts.prefix_hash(
        prompts.build_messages(system="S", tools="T", shared=["doc"], task="other question"))


def test_affinity_header_only_when_session_exists():
    assert prompts.affinity_headers(None) == {}
    assert prompts.affinity_headers("s1") == {"x-session-affinity": "s1"}
    assert prompts.hashed("s1") != "s1"  # receipts keep a hash, not the id
    assert prompts.cached_share(0, 0) == 0.0 and prompts.cached_share(200, 50) == 0.25


def test_energy_is_a_range_that_scales_with_tokens():
    lo, hi = energy.wh_range(1000, 0, 4000)
    assert 0 < lo < hi and hi / lo == pytest.approx(10)
    assert energy.wh_range(1000, 1000, 4000)[0] < lo  # cached prompt is not recomputed
    est = energy.estimate({"prompt": 100, "cached": 0, "completion": 1000},
                          {"prompt": 100, "cached": 0, "completion": 5000})
    assert est["label"] == "ESTIMATE" and est["saved_wh"]["low"] > 0


# ---- transport ------------------------------------------------------------------------------

class FakeResp:
    def __init__(self, status, body, headers=None):
        self.status, self._b, self.headers = status, body, headers or {}

    def read(self):
        return self._b if isinstance(self._b, bytes) else json.dumps(self._b).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _req(model=MODEL):
    return tr.Request("t|low|0", {"model": model, "messages": []})


def test_missing_key_message_has_no_secret_and_names_the_variable():
    with pytest.raises(tr.MissingKey, match="BASETEN_API_KEY"):
        tr.LiveTransport(env={})


def test_live_transport_hides_key_and_refuses_other_models(monkeypatch):
    t = tr.LiveTransport(min_interval_s=0, env={"BASETEN_API_KEY": KEY})
    assert KEY not in repr(t) and "SECRET" not in repr(t)
    assert tr.key_prefix(KEY) == "abc123" and tr.key_prefix("nodots") is None
    monkeypatch.setattr(tr.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("must not reach the network"))
    with pytest.raises(tr.CloudError, match="refusing to send model"):
        t(_req("some/other-model"))


def test_live_transport_sends_bearer_and_parses(monkeypatch):
    seen = {}

    def fake(req, timeout):
        seen["auth"], seen["url"] = req.headers["Authorization"], req.full_url
        seen["aff"] = req.headers.get("X-session-affinity")
        return FakeResp(200, {"choices": []}, {"x-ratelimit-remaining-requests": "9"})

    monkeypatch.setattr(tr.urllib.request, "urlopen", fake)
    t = tr.LiveTransport(min_interval_s=0, env={"BASETEN_API_KEY": KEY})
    rep = t(tr.Request("t", {"model": MODEL}, {"x-session-affinity": "s9"}))
    assert rep.status == 200 and rep.headers == {"x-ratelimit-remaining-requests": "9"}
    assert seen["auth"] == f"Bearer {KEY}" and seen["url"] == BASE_URL + "/chat/completions"
    assert seen["aff"] == "s9"


def test_429_backs_off_then_succeeds_and_counts_retries(monkeypatch):
    calls, waits = [], []

    def fake(req, timeout):
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.HTTPError("u", 429, "slow", {"Retry-After": "2"}, io.BytesIO(b"{}"))
        return FakeResp(200, {"ok": 1})

    monkeypatch.setattr(tr.urllib.request, "urlopen", fake)
    t = tr.LiveTransport(min_interval_s=0, env={"BASETEN_API_KEY": KEY}, sleep=waits.append)
    rep = t(_req())
    assert rep.status == 200 and rep.retries == 2 and waits == [2.0, 2.0]


def test_errors_never_carry_the_key(monkeypatch):
    def boom(req, timeout):
        raise urllib.error.URLError(f"cannot reach host with {KEY}")

    monkeypatch.setattr(tr.urllib.request, "urlopen", boom)
    t = tr.LiveTransport(min_interval_s=0, env={"BASETEN_API_KEY": KEY})
    with pytest.raises(tr.CloudError) as e:
        t(_req())
    assert KEY not in str(e.value) and "SECRET" not in str(e.value)
    monkeypatch.setattr(tr.urllib.request, "urlopen",
                        lambda *a, **k: FakeResp(500, f"<html>{KEY}</html>".encode()))
    assert KEY not in json.dumps(t(_req()).body)


def test_recorder_drops_reasoning_text_and_fixture_replays(tmp_path):
    body = {"choices": [{"message": {"content": "ANSWER: 4",
                                     "reasoning_content": "private chain of thought"}}]}
    inner = lambda req: tr.Reply(200, body, 1.5, {}, 0, 100.0)  # noqa: E731
    rec = tr.Recorder(inner)
    rec(tr.Request("a|low|0", {}))
    p = rec.save(tmp_path / "f.json")
    text = p.read_text()
    assert "private chain" not in text and "reasoning_chars" in text
    rep = tr.FixtureTransport.load(p)(tr.Request("a|low|0", {}))
    assert rep.status == 200 and rep.latency_s == 1.5
    with pytest.raises(tr.CloudError, match="no reply"):
        tr.FixtureTransport.load(p)(tr.Request("missing", {}))
    with pytest.raises(tr.CloudError, match="not a cloud fixture"):
        tr.FixtureTransport({"format": "x"})


def test_live_transport_spaces_calls_apart():
    now = [100.0]
    waits: list[float] = []

    def sleep(s):
        waits.append(s)
        now[0] += s

    t = tr.LiveTransport(env={"BASETEN_API_KEY": KEY}, sleep=sleep, clock=lambda: now[0],
                         min_interval_s=4.0)
    t._once = lambda data, headers: (200, b"{}", {})
    req = tr.Request("a", {"model": tr.MODEL})
    t(req)
    assert waits == []  # the first call goes straight out
    now[0] += 1.0
    t(req)
    assert waits == [3.0]  # the second waits out the rest of the 4 s gap
    now[0] += 10.0
    t(req)
    assert waits == [3.0]  # already far enough apart
