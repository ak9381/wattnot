"""The Meta API spend guard, client, agent decisions and the `wattnot meta spend` command."""

from __future__ import annotations

import json
import threading

import pytest

from wattnot.meta import agent as ag
from wattnot.meta import cli as meta_cli
from wattnot.meta.client import GuardedClient, MetaError, load_key
from wattnot.meta.ledger import (
    ASSUMED,
    BudgetExceeded,
    SpendLedger,
    cost_usd,
    estimate,
    price_for,
)
from wattnot.meta.scenarios import SCENARIOS

SECRET = "sk-test-NOT-A-REAL-KEY-123456"


def body(max_tokens=1000, text="hello", model="muse-spark-1.3"):
    return {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": text}]}


def ok_response(prompt=100, completion=200, cached=0, reasoning=150, content="ok"):
    return {"model": "muse-spark-1.3", "choices": [{"message": {"content": content},
                                                    "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                      "prompt_tokens_details": {"cached_tokens": cached},
                      "completion_tokens_details": {"reasoning_tokens": reasoning}}}


class FakeNet:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, req, timeout):
        self.calls.append(json.loads(req.data))
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, Exception):
            raise r
        status, data = r
        return status, json.dumps(data).encode()


@pytest.fixture
def ledger(tmp_path):
    return SpendLedger(tmp_path / "meta_spend.json")


# ---- prices --------------------------------------------------------------------------------

def test_price_arithmetic_matches_published_rates():
    # $1.25/M uncached in, $0.15/M cached in, $4.25/M out
    assert cost_usd("muse-spark-1.3", 1_000_000, 0) == pytest.approx(1.25)
    assert cost_usd("muse-spark-1.3", 1_000_000, 0, cached_tokens=1_000_000) == pytest.approx(0.15)
    assert cost_usd("muse-spark-1.3", 0, 1_000_000) == pytest.approx(4.25)
    assert cost_usd("muse-spark-1.3", 1000, 2000, 400) == pytest.approx(
        (600 * 1.25 + 400 * 0.15 + 2000 * 4.25) / 1e6)


def test_contributor_and_unknown_models():
    assert price_for("muse-spark-1.3-contributor")[0].output_per_m == 0.20
    p, assumed = price_for("some-new-model")
    assert assumed and p == ASSUMED and p.output_per_m >= 20  # highest published rate
    assert price_for("muse-spark-1.3-20261101")[1] is False  # dated id uses the base price


def test_estimate_is_an_upper_bound():
    b = body(max_tokens=1000, text="x" * 3000)
    n_in, n_out, usd = estimate(b)
    assert n_out == 1000 and n_in >= 3000 / 4  # at most 4 chars per token in real text
    assert usd >= cost_usd("muse-spark-1.3", 800, 1000)


# ---- the ledger guard ----------------------------------------------------------------------

def test_reserve_then_settle_records_real_usage(ledger):
    eid, soft = ledger.reserve(body(), "unit")
    assert not soft and ledger.entries()[0]["status"] == "pending"
    e = ledger.settle(eid, ok_response()["usage"])
    assert e["usd"] == pytest.approx(cost_usd("muse-spark-1.3", 100, 200))
    assert e["reasoning_tokens"] == 150 and not e["estimated"]
    assert ledger.total() == pytest.approx(e["usd"])


def test_refuses_when_total_plus_estimate_passes_the_cap(tmp_path):
    led = SpendLedger(tmp_path / "l.json", cap_usd=0.05, soft_usd=0.03)
    n_in, n_out, usd = estimate(body(max_tokens=2000))
    n = int(0.05 // usd)
    for _ in range(n):
        led.reserve(body(max_tokens=2000), "fill")
    spent = led.total()
    with pytest.raises(BudgetExceeded):
        led.reserve(body(max_tokens=2000), "one-too-many")
    assert led.total() == spent  # a refused call writes nothing
    assert spent <= 0.05


def test_soft_warning_between_15_and_20(tmp_path):
    led = SpendLedger(tmp_path / "l.json")
    led.seed([{"key": "a", "model": "muse-spark-1.3", "purpose": "x",
               "input_tokens": 0, "output_tokens": int(14.9 / 4.25 * 1e6)}])
    assert led.total() == pytest.approx(14.9, abs=0.01)
    _, soft = led.reserve(body(max_tokens=100), "small")
    assert not soft  # still under 15 with the estimate
    _, soft = led.reserve(body(max_tokens=100_000), "big")  # $0.425 worst case
    assert soft
    with pytest.raises(BudgetExceeded):
        led.reserve(body(max_tokens=2_000_000), "huge")  # +$8.50 worst case


def test_exactly_at_cap_is_allowed_and_over_is_not(tmp_path):
    led = SpendLedger(tmp_path / "l.json", cap_usd=1.0, soft_usd=0.5)
    led.seed([{"key": "a", "model": "muse-spark-1.3", "purpose": "x", "input_tokens": 0,
               "output_tokens": int(0.99 / 4.25 * 1e6)}])
    with pytest.raises(BudgetExceeded):
        led.reserve(body(max_tokens=10_000), "over")  # $0.0425 worst case


def test_rejected_call_costs_nothing_and_unknown_outcome_keeps_worst_case(ledger):
    a, _ = ledger.reserve(body(), "rejected")
    ledger.settle(a, None, status="error", http_status=400)
    b, _ = ledger.reserve(body(), "timeout")
    est = estimate(body())[2]
    e = ledger.settle(b, None, status="unknown")
    assert e["usd"] == pytest.approx(est) and e["estimated"]
    assert ledger.total() == pytest.approx(est, abs=1e-5)


def test_parallel_callers_cannot_squeeze_past_the_cap(tmp_path):
    led = SpendLedger(tmp_path / "l.json", cap_usd=0.2, soft_usd=0.1)
    done, refused = [], []

    def go():
        try:
            led.reserve(body(max_tokens=2000), "par")
            done.append(1)
        except BudgetExceeded:
            refused.append(1)

    ts = [threading.Thread(target=go) for _ in range(40)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert refused and led.total() <= 0.2
    assert len(done) == len(led.entries())


def test_seed_is_idempotent_and_survives_reopen(tmp_path):
    p = tmp_path / "l.json"
    row = {"key": "k1", "model": "muse-spark-1.3", "purpose": "earlier", "input_tokens": 1000,
           "output_tokens": 2000}
    assert SpendLedger(p).seed([row]) == 1
    assert SpendLedger(p).seed([row]) == 0
    assert SpendLedger(p).total() == pytest.approx(cost_usd("muse-spark-1.3", 1000, 2000))


# ---- the client ----------------------------------------------------------------------------

def test_client_logs_a_good_call_and_never_stores_the_key(ledger):
    net = FakeNet((200, ok_response(cached=40)))
    c = GuardedClient(key=SECRET, ledger=ledger, opener=net)
    r = c(body(), purpose="unit")
    assert r["choices"][0]["message"]["content"] == "ok"
    e = ledger.entries()[0]
    assert e["purpose"] == "unit" and e["status"] == "ok" and e["cached_tokens"] == 40
    assert SECRET not in ledger.path.read_text()


def test_client_adds_an_output_cap_when_none_is_set(ledger):
    net = FakeNet((200, ok_response()))
    GuardedClient(key=SECRET, ledger=ledger, opener=net)(
        {"model": "muse-spark-1.3", "messages": []}, purpose="u")
    assert net.calls[0]["max_completion_tokens"] == 2000


def test_client_refuses_without_calling_the_network_when_over_cap(tmp_path):
    led = SpendLedger(tmp_path / "l.json", cap_usd=0.001, soft_usd=0.0005)
    net = FakeNet((200, ok_response()))
    with pytest.raises(BudgetExceeded):
        GuardedClient(key=SECRET, ledger=led, opener=net)(body(max_tokens=5000), purpose="u")
    assert net.calls == [] and led.entries() == []


def test_client_http_error_is_logged_at_zero(ledger):
    net = FakeNet((404, {"error": {"message": "model not found"}}))
    with pytest.raises(MetaError, match="HTTP 404"):
        GuardedClient(key=SECRET, ledger=ledger, opener=net)(body(), purpose="u")
    e = ledger.entries()[0]
    assert e["status"] == "error" and e["usd"] == 0 and e["http_status"] == 404


def test_client_timeout_keeps_the_worst_case(ledger):
    net = FakeNet(TimeoutError())
    with pytest.raises(MetaError):
        GuardedClient(key=SECRET, ledger=ledger, opener=net)(body(), purpose="u")
    e = ledger.entries()[0]
    assert e["status"] == "unknown" and e["usd"] == pytest.approx(estimate(body())[2])


def test_client_warns_over_the_soft_limit(tmp_path):
    led = SpendLedger(tmp_path / "l.json", cap_usd=1.0, soft_usd=0.0001)
    net = FakeNet((200, ok_response()))
    with pytest.warns(UserWarning, match="soft limit"):
        GuardedClient(key=SECRET, ledger=led, opener=net)(body(), purpose="u")


def test_no_key_is_an_error_before_any_ledger_write(ledger):
    with pytest.raises(MetaError, match="no API key"):
        GuardedClient(key="", ledger=ledger, opener=FakeNet())(body(), purpose="u")
    assert ledger.entries() == []


def test_load_key_prefers_env_then_the_login_file(tmp_path, monkeypatch):
    f = tmp_path / "auth.json"
    f.write_text(json.dumps({"providers": {"meta": {"api_key": "from-file"}}}))
    monkeypatch.delenv("META_API_KEY", raising=False)
    assert load_key(f) == "from-file"
    monkeypatch.setenv("META_API_KEY", "from-env")
    assert load_key(f) == "from-env"
    monkeypatch.delenv("META_API_KEY")
    assert load_key(tmp_path / "missing.json") == ""


def test_spark_client_goes_through_the_guard(tmp_path, monkeypatch):
    from wattnot.planner import spark

    led = SpendLedger(tmp_path / "l.json")
    net = FakeNet((200, ok_response(content="{}")))
    monkeypatch.setattr("wattnot.meta.client._urlopen", net)
    c = spark.SparkClient(key=SECRET, ledger=led, purpose="judge")
    c({"model": "muse-spark-1.3", "max_tokens": 50, "messages": []})
    assert led.entries()[0]["purpose"] == "judge" and len(net.calls) == 1


def test_a_refused_planner_call_falls_back(tmp_path, monkeypatch):
    from wattnot.planner import spark

    led = SpendLedger(tmp_path / "l.json", cap_usd=0.0001, soft_usd=0.00005)
    net = FakeNet((200, ok_response()))
    monkeypatch.setattr("wattnot.meta.client._urlopen", net)
    client = spark.SparkClient(key=SECRET, ledger=led)
    dm = spark.DecisionMaker(client, client=client)
    s = {"cap_w": 400, "range_w": [200, 700], "step_w": 25, "p99_headroom_pct": 20.0,
         "last_gates": [], "knobs": {}}
    d = dm.decide(s)
    assert d.model == "fallback" and net.calls == []


# ---- agent decisions -----------------------------------------------------------------------

class Scripted:
    def __init__(self, *contents):
        self.contents, self.purposes = list(contents), []

    def __call__(self, body, *, purpose):
        self.purposes.append(purpose)
        return ok_response(content=json.dumps(self.contents.pop(0)))


def test_next_trial_accepts_only_allowed_settings():
    sc = SCENARIOS["gpu-power"]
    good = Scripted({"kind": "try", "knob": "power_cap_w", "value": 600, "reason": "p99 ok"})
    d = ag.next_trial(good, sc.prompt_view(), [])
    assert d["knob"] == "power_cap_w" and d["value"] == 600
    assert good.purposes == ["next-knob:gpu-power"]
    for bad in ({"kind": "try", "knob": "power_cap_w", "value": 123, "reason": "x"},
                {"kind": "try", "knob": "made_up", "value": 1, "reason": "x"}):
        with pytest.raises(ValueError):
            ag.next_trial(Scripted(bad), sc.prompt_view(), [])
    with pytest.raises(ValueError):  # repeats a tried setting
        ag.next_trial(good2(), sc.prompt_view(), [{"setting": {"power_cap_w": 600}}])


def good2():
    return Scripted({"kind": "try", "knob": "power_cap_w", "value": 600, "reason": "again"})


def test_effort_order_fills_in_missing_levels():
    c = Scripted({"orders": {"a": ["medium"], "b": ["low", "low", "bogus", "minimal"]},
                  "note": "n"})
    r = ag.effort_order(c, {"a": {"items": 1}, "b": {"items": 1}})
    assert r["orders"]["a"] == ["medium", "minimal", "low"]
    assert r["orders"]["b"] == ["low", "minimal", "medium"]


def test_scenario_surfaces_are_labelled_and_gated():
    for sc in SCENARIOS.values():
        m = sc.run(sc.start)
        assert m["label"] == "SIMULATED" and m["passed"] and m["saving_pct"] == 0
    assert not SCENARIOS["moe-routing"].run({"expert_keep_fraction": 0.5,
                                             "adaptive_top_k": "on"})["passed"]


# ---- CLI -----------------------------------------------------------------------------------

def test_meta_spend_cli_shows_total_breakdown_and_remaining(tmp_path, capsys, monkeypatch):
    from wattnot.cli import main

    monkeypatch.setattr(meta_cli, "EARLIER", ())

    p = tmp_path / "l.json"
    led = SpendLedger(p)
    for purpose in ("judge", "judge", "effort-order"):
        eid, _ = led.reserve(body(), purpose)
        led.settle(eid, ok_response(prompt=1000, completion=2000)["usage"])
    assert main(["meta", "spend", "--ledger", str(p)]) == 0
    out = capsys.readouterr().out
    assert "of $20.00 hard cap" in out and "remaining" in out
    assert "judge" in out and "effort-order" in out
    spent = led.total()
    assert f"${spent:.4f}" in out and f"${20 - spent:.4f}" in out
    assert main(["meta", "spend", "--ledger", str(p), "--json"]) == 0
    j = json.loads(capsys.readouterr().out)
    assert j["calls"] == 3 and j["per_purpose"]["judge"]["calls"] == 2


def test_earlier_recorded_usage_is_counted_once(tmp_path):
    led = SpendLedger(tmp_path / "l.json")
    n = meta_cli.seed_earlier(led)
    assert n == 2 and 0 < led.total() < 0.5  # about 15k tokens in total
    assert meta_cli.seed_earlier(led) == 0


# ---- the session, offline ------------------------------------------------------------------

def test_session_next_knob_stops_cleanly_when_the_guard_refuses(tmp_path):
    from wattnot.meta.session import Session

    led = SpendLedger(tmp_path / "l.json", cap_usd=0.0005, soft_usd=0.0002)
    net = FakeNet((200, ok_response(content=json.dumps(
        {"kind": "try", "knob": "power_cap_w", "value": 600, "reason": "r"}))))
    client = GuardedClient(key=SECRET, ledger=led, opener=net)
    out = Session(client, led, log=lambda m: None).run(("next-knob",))
    assert out["stopped"].startswith("refused") and out["spend"]["total_usd"] <= 0.0005


def test_session_effort_ladder_picks_the_cheapest_level_that_matches_the_reference(tmp_path):
    from wattnot.meta.session import Session

    led = SpendLedger(tmp_path / "l.json")

    def opener(req, timeout):
        b = json.loads(req.data)
        if "response_format" in b:  # the effort-order call
            names = list(b["response_format"]["json_schema"]["schema"]["properties"]["orders"]
                         ["properties"])
            content = json.dumps({"orders": {n: ["minimal", "low", "medium"] for n in names},
                                  "note": "cheap first"})
        else:  # the workload items: answer the same at every level, always correct
            content = "ANSWER: 48213|67.40 SELECT 1 ok"
        return 200, json.dumps(ok_response(content=content, completion=100)).encode()

    client = GuardedClient(key=SECRET, ledger=led, opener=opener)
    res = Session(client, led, repeats=1, workers=2, log=lambda m: None).effort_ladder()
    assert all(v["picked"] == "minimal" for v in res["classes"].values() if
               v["levels"]["high"]["pass_rate"] == 100.0)
    assert res["orders"]["extract"] == ["minimal", "low", "medium"]
    assert all(e["purpose"] != "unspecified" for e in led.entries())


def test_judge_calibration_scores_against_the_human_labels(tmp_path):
    from wattnot.gates.judge import load_judge_set
    from wattnot.meta.session import Session

    led = SpendLedger(tmp_path / "l.json")
    labels = {i["id"]: ("worse" if i["label"] == "worse" else "same") for i in load_judge_set()}

    def opener(req, timeout):
        content = json.dumps({"verdicts": [{"id": k, "verdict": v} for k, v in labels.items()]})
        return 200, json.dumps(ok_response(content=content)).encode()

    client = GuardedClient(key=SECRET, ledger=led, opener=opener)
    res = Session(client, led, log=lambda m: None).judge_calibration(("low",))
    assert res["low"]["agreement"] == 1.0 and res["low"]["worse_missed"] == 0
    assert led.entries()[0]["purpose"] == "judge-calibration:low"
