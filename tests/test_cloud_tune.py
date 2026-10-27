"""The reasoning-effort router: the gates decide, the planner only proposes an order."""

import json

import pytest

from wattnot.cloud import MODEL
from wattnot.cloud import run as rn
from wattnot.cloud import tune as tn
from wattnot.cloud.pricing import Rates
from wattnot.cloud.transport import FixtureTransport, Reply, Request
from wattnot.runner.workload import load_workload

ITEMS = list(load_workload(rn.WORKLOAD).items)
RATES = Rates()


def reply(content, *, prompt=100, completion=500, reasoning=None, latency=2.0, finish="stop",
          cached=0, status=200):
    usage = {"prompt_tokens": prompt, "completion_tokens": completion,
             "prompt_tokens_details": {"cached_tokens": cached},
             "completion_tokens_details": {"reasoning_tokens": reasoning}}
    msg = {"content": content}
    return Reply(status, {"choices": [{"message": msg, "finish_reason": finish}],
                          "usage": usage}, latency, {}, 0, 1_790_000_000.0)


def model(wrong=(), truncated=(), tokens=None, lat=None):
    """A fake model: right unless (id, effort) is listed; cost grows with effort."""

    tokens = tokens or {"low": 300, "medium": 900, "high": 2500, "xhigh": 6000}
    lat = lat or {"low": 2, "medium": 5, "high": 12, "xhigh": 30}
    gold = {i.id: i.meta["gold"] for i in ITEMS}

    def send(req: Request) -> Reply:
        if req.tag == "propose":
            return reply('{"orders": {}}')
        tid, effort, _ = req.tag.split("|")
        t, la = tokens[effort], lat[effort]
        if (tid, effort) in truncated:
            return reply("", completion=t * 3, finish="length", latency=la)
        ans = "ANSWER: nope" if (tid, effort) in wrong else f"ANSWER: {gold[tid]}"
        return reply(ans, completion=t, latency=la)

    return send


def run_tune(send, *, classes=("arith",), order=("low", "medium", "high"), **kw):
    runner = tn.Runner(send, RATES)
    prop = {"orders": {c: list(order) for c in classes}, "proposer": "test", "note": "",
            "tokens": {"input": 0, "output": 0}, "usd": 0.0}
    return tn.tune(ITEMS, runner, proposal=prop, classes=list(classes), repeats=1, **kw), runner


def test_workload_loads_with_five_classes():
    assert len(ITEMS) == 15
    assert {i.meta["class"] for i in ITEMS} == {"extract", "classify", "trace", "arith", "logic"}


def test_keeps_lowest_level_that_passes_and_skips_higher_ones():
    res, _ = run_tune(model())
    c = res["classes"][0]
    assert c["chosen_effort"] == "low" and c["kept"]
    assert [t["skipped"] for t in c["tried"]] == [False, True, True]
    assert c["saving_pct"] > 50  # same answers, far fewer output tokens


def test_a_wrong_answer_at_low_moves_the_choice_up():
    res, _ = run_tune(model(wrong={("arith-1", "low")}))
    c = res["classes"][0]
    assert c["chosen_effort"] == "medium"
    low = c["tried"][0]
    assert not low["passed"] and "fixed" in low["failed_gates"]


def test_truncated_reasoning_counts_as_a_failed_answer():
    res, _ = run_tune(model(truncated={("arith-2", "low")}))
    c = res["classes"][0]
    assert c["tried"][0]["truncated"] == 1 and not c["tried"][0]["passed"]
    assert c["chosen_effort"] == "medium"


def test_nothing_passes_falls_back_to_xhigh_with_zero_saving():
    bad = {(i.id, e) for i in ITEMS for e in ("low", "medium", "high")}
    res, _ = run_tune(model(wrong=bad))
    c = res["classes"][0]
    assert c["chosen_effort"] == "xhigh" and not c["kept"] and c["saving_pct"] == 0.0


def test_escape_retry_is_charged_to_latency_and_cost():
    # one wrong answer out of three keeps quality inside a loose gate, but the xhigh retry
    # must show up in the p99 and in the spend.
    res, _ = run_tune(model(wrong={("arith-1", "low")}), max_drop_pts=40.0, p99_slack=10.0)
    c = res["classes"][0]
    assert c["chosen_effort"] == "low"
    assert c["escape_retry_rate"] == pytest.approx(1 / 3, abs=1e-3)
    low = c["tried"][0]
    assert 30 < low["p99_effective_s"] <= 32  # low latency + the xhigh retry
    assert c["measured"]["usd_with_escape"] > c["measured"]["usd"]


def test_p99_gate_rejects_a_level_that_is_not_faster():
    slow = {"low": 400, "medium": 400, "high": 400, "xhigh": 30}
    res, _ = run_tune(model(lat=slow))
    assert res["classes"][0]["tried"][0]["failed_gates"] == ["p99"]


def test_any_proposed_order_reaches_the_same_choice():
    a, _ = run_tune(model(wrong={("arith-1", "low")}), order=("low", "medium", "high"))
    b, _ = run_tune(model(wrong={("arith-1", "low")}), order=("high", "medium", "low"))
    assert a["classes"][0]["chosen_effort"] == b["classes"][0]["chosen_effort"] == "medium"


def test_budget_stops_before_the_limit():
    runner = tn.Runner(model(), RATES, budget_usd=0.001)
    prop = {"orders": {"arith": ["low", "medium", "high"]}, "proposer": "t", "note": "",
            "tokens": {"input": 0, "output": 0}, "usd": 0.0}
    res = tn.tune(ITEMS, runner, proposal=prop, classes=["arith"], repeats=1)
    assert res["stopped"] and "stopping at 80%" in res["stopped"]
    assert res["classes"][0]["chosen_effort"] == "xhigh" and res["classes"][0]["saving_pct"] == 0


def test_planner_failure_falls_back_to_a_fixed_order():
    by = {"arith": [i for i in ITEMS if i.meta["class"] == "arith"]}

    def broken(req):
        return reply("sorry, no JSON here")

    p = tn.propose(by, broken, RATES)
    assert p["proposer"] == "fallback" and p["orders"]["arith"] == ["low", "medium", "high"]
    assert tn.propose(by, None, RATES)["proposer"] == "fallback"


def test_planner_order_is_used_when_valid():
    by = {"arith": [i for i in ITEMS if i.meta["class"] == "arith"]}
    good = lambda req: reply('{"orders": {"arith": ["high","low"]}, "note": "go high"}')  # noqa: E731
    p = tn.propose(by, good, RATES)
    assert p["orders"]["arith"] == ["high", "low", "medium"] and p["usd"] > 0


def test_request_body_is_what_the_api_expects():
    runner = tn.Runner(model(), RATES)
    body = runner.body_for(ITEMS[0], "low")
    assert body["model"] == MODEL and body["reasoning_effort"] == "low" and body["temperature"] == 0
    assert body["max_tokens"] < runner.body_for(ITEMS[0], "xhigh")["max_tokens"]
    assert body["messages"][0]["role"] == "system" and body["messages"][-1]["role"] == "user"
    assert "stop" in body and "stop" not in runner.body_for(ITEMS[0], "xhigh")


def test_session_affinity_is_per_class_and_can_be_turned_off():
    _, runner = run_tune(model(), classes=("arith", "logic"))
    assert len({c.session for c in runner.calls if c.cls == "arith"}) == 1
    assert len({c.session for c in runner.calls}) == 2
    off2 = tn.Runner(model(), RATES, affinity=False)
    off2.call(ITEMS[0], "low", 0)
    assert off2.calls[0].session is None


def test_reasoning_tokens_prefer_api_then_text_then_absent():
    req = Request("x", {})
    r = tn.parse_reply(reply("ANSWER: 1", reasoning=120), req=req, task_id="a", cls="c",
                       effort="low", repeat=0, rates=RATES)
    assert (r.reasoning, r.reasoning_source) == (120, "api")
    body = {"choices": [{"message": {"content": "x", "reasoning_content": "y" * 400},
                         "finish_reason": "stop"}], "usage": {"completion_tokens": 200}}
    r = tn.parse_reply(Reply(200, body, 1, {}), req=req, task_id="a", cls="c", effort="low",
                       repeat=0, rates=RATES)
    assert (r.reasoning, r.reasoning_source) == (100, "estimated_from_text")
    r = tn.parse_reply(reply("x"), req=req, task_id="a", cls="c", effort="low", repeat=0,
                       rates=RATES)
    assert r.reasoning is None and r.reasoning_source == "absent"
    assert r.fields["reasoning_tokens"] is False


def test_call_rows_carry_numbers_and_hashes_but_no_text():
    _, runner = run_tune(model())
    row = runner.calls[0].row()
    assert "content" not in row and row["answer_sha256"] and len(row["answer_sha256"]) == 16
    assert "ANSWER" not in json.dumps([c.row() for c in runner.calls])


def test_replaying_the_demo_fixture_is_deterministic(tmp_path):
    ft = FixtureTransport.load(rn.DEMO_FIXTURE)
    a = rn.run(rn.Options(), ft, "replay-synthetic", out_dir=tmp_path / "a",
               now=1_790_000_000.0)[1]
    b = rn.run(rn.Options(), FixtureTransport.load(rn.DEMO_FIXTURE), "replay-synthetic",
               out_dir=tmp_path / "b", now=1_790_000_000.0)[1]
    assert [c["chosen_effort"] for c in a["classes"]] == [c["chosen_effort"] for c in b["classes"]]
    assert a["totals"] == b["totals"]
    got = {c["class"]: c["chosen_effort"] for c in a["classes"]}
    assert got == {"extract": "low", "classify": "low", "trace": "low", "arith": "medium",
                   "logic": "high"}
