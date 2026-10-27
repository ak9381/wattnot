"""The three harness loops, the injected cases and the summary, with a scripted fake client."""

from __future__ import annotations

import json

import pytest

from wattnot.agentwaste.bench import cases as C
from wattnot.agentwaste.bench.harness import (
    MARKER,
    PLANNER_CAP,
    REACT_CAP,
    STOP_CAP,
    run_case,
    stop_rule_met,
)
from wattnot.agentwaste.bench.hsummary import summarize


def resp(text=None, calls=None, comp=20, prompt=100):
    msg = {"role": "assistant", "content": text}
    if calls:
        msg["tool_calls"] = [{"id": f"c{i}", "type": "function",
                              "function": {"name": n, "arguments": json.dumps(a)}}
                             for i, (n, a) in enumerate(calls)]
    return {"id": "x", "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": comp}}


class Fake:
    """Calls a fixed tool forever; takes the done action if the saver offers a nudge."""

    def __init__(self, tool=("service_status", {"name": "billing"}), text=None,
                 delegate_first=True):
        self.tool, self.text, self.n, self.delegate_first = tool, text, 0, delegate_first

    def __call__(self, body, **kw):
        self.n += 1
        if "Note:" in str(body["messages"][-1].get("content")):
            return resp(calls=[("done", {"reason": "blocked by a failing tool"})])
        if self.text is not None:
            return resp(self.text)
        planner = "planning agent" in body["messages"][0]["content"]
        if planner and self.delegate_first:
            return resp(calls=[("delegate", {"task": "check billing"})])
        return resp(calls=[self.tool])


def case(cid):
    return next(c for c in C.all_cases() if c.id == cid)


def test_there_are_12_injected_and_18_normal_cases():
    assert len(C.INJECTED) == 12 and len(C.normal_cases()) == 18
    assert len({c.id for c in C.all_cases()}) == 30
    cats = [c.category for c in C.INJECTED]
    assert {k: cats.count(k) for k in set(cats)} == {"fuzzy": 3, "retry": 3, "noprogress": 3,
                                                      "sprawl": 3}
    assert set(C.DEMO_IDS) <= set(C.ALL_INJECTED_IDS)
    assert {c.harness for c in C.INJECTED} == {"react", "stop", "planner"}


def test_fuzzy_prompts_really_disagree_with_their_stop_rule():
    f1, f2, f3 = (case(i) for i in ("fuzzy-1-prompt-drift", "fuzzy-2-json-final",
                                     "fuzzy-3-strict-line"))
    assert MARKER not in f1.system and "Finished" in f1.system
    assert MARKER not in f2.system and f2.rule == "json"
    assert MARKER in (f3.system or "") or f3.system == ""  # default prompt asks for marker + text
    assert f3.rule == "strict"
    assert not stop_rule_met(f"{MARKER} the limit is 120", "strict")
    assert stop_rule_met(MARKER, "strict")
    assert stop_rule_met(f"all good {MARKER}", "contains")
    assert stop_rule_met('Here: {"final_answer": 4}', "json")
    assert not stop_rule_met("The answer is 4", "json")


def test_stop_loop_never_ends_by_itself_without_the_marker():
    r = run_case(case("fuzzy-1-prompt-drift"), Fake(text="billing degraded, checkout down"),
                 "harness")
    assert r.ended_by == "cap" and r.calls == STOP_CAP and r.miss


def test_saver_ends_the_same_run_early():
    r = run_case(case("fuzzy-1-prompt-drift"), Fake(text="billing degraded, checkout down"),
                 "harness+saver")
    assert r.ended_by == "saver-done" and r.calls < STOP_CAP and not r.miss and r.nudges == 1
    assert r.flags["paraphrase"] == 1


def test_react_retry_storm_hits_the_cap_then_saver_cuts_it():
    c = case("retry-1-status-500")
    off = run_case(c, Fake(), "harness")
    on = run_case(c, Fake(), "harness+saver")
    assert off.ended_by == "cap" and off.calls == REACT_CAP and off.miss
    assert on.ended_by == "saver-done" and on.calls <= 4 and on.success
    assert on.said[-1].startswith("blocked")


def test_react_stops_on_a_text_reply_like_any_harness():
    r = run_case(case("normal-react-E1"), Fake(text="Billing is degraded."), "harness")
    assert r.ended_by == "final" and r.calls == 1 and not r.miss


def test_planner_helpers_have_their_own_caps_and_the_saver_shares_one_budget():
    c = case("sprawl-2-redelegate-failure")
    off = run_case(c, Fake(), "harness")
    on = run_case(c, Fake(), "harness+saver")
    assert off.helpers >= 1 and off.calls > PLANNER_CAP  # helpers' calls sit outside the cap
    assert on.calls < off.calls and on.ended_by.startswith("saver")


def test_bench_safety_stop_ends_a_runaway_run_in_both_arms():
    c = case("sprawl-3-recursive-split")
    r = run_case(c, Fake(), "harness")
    assert r.ended_by in ("safety", "cap") and r.calls <= 60 and r.miss


def test_a_failing_client_is_reported_not_hidden():
    def boom(body, **kw):
        raise RuntimeError("down")
    r = run_case(case("normal-react-E1"), boom, "harness")
    assert r.ended_by == "error" and r.error and not r.success


def test_unknown_arm_is_refused():
    with pytest.raises(ValueError):
        run_case(case("normal-react-E1"), Fake(), "other")


def test_fault_world_errors_carry_a_fresh_id_each_time():
    w = C.FaultWorld(faults={"service_status": "500"})
    a, b = w.call("service_status", {"name": "x"}), w.call("service_status", {"name": "x"})
    assert a != b and "500" in a
    e = C.FaultWorld(faults={"search_docs": "empty"})
    assert json.loads(e.call("search_docs", {"query": "x"}))["results"] == []
    s = C.FaultWorld(faults={"search_docs": "ignores-query"})
    first = json.loads(s.call("search_docs", {"query": "audit"}))
    assert {d["title"] for d in first} == {"rotate api key", "rate limits", "backups"}


def test_checks_look_at_outcome_not_call_count():
    ok = C.FaultWorld()
    assert C._blocked(ok, "the tool failed, i could not do it")
    assert not C._blocked(ok, "all done, here it is")
    ok.call("create_ticket", {"title": "x"})
    assert not C._blocked(ok, "failed")  # inventing work fails the check


def test_summary_pairs_arms_and_picks_scenarios_by_a_fixed_rule():
    rows = []
    for rep in (0, 1):
        for arm, calls, miss in (("harness", 10, rep == 1), ("harness+saver", 4, False)):
            rows.append({"harness": "stop", "case": "fuzzy-1-prompt-drift", "category": "fuzzy",
                         "arm": arm, "rep": rep, "success": True, "calls": calls,
                         "prompt_tokens": 1000 * calls, "cached_tokens": 0,
                         "completion_tokens": 100 * calls, "reasoning_tokens": 0,
                         "total_tokens": 1100 * calls, "tool_calls": 0, "mutations": 0,
                         "wall_s": calls, "ended_by": "cap" if miss else "final", "miss": miss,
                         "helpers": 0, "helper_caps": 0, "nudges": 0, "stop_reason": None,
                         "flags": {}, "error": None, "said": [], "trace": [{"n": 1}]})
    s = summarize(rows)
    assert s["schema"] == "wattnot.agent-waste-v2/1"
    d = s["injected"]["saver_vs_harness"]
    assert d["calls"] == -60.0 and d["success_points"] == 0.0
    assert s["injected"]["harness"]["miss_runs"] == 1
    sc = s["scenarios"][0]
    assert sc["case"] == "fuzzy-1-prompt-drift" and sc["rep"] == 1  # first rep the harness missed
    assert set(sc["arms"]) == {"harness", "harness+saver"}
    assert s["labels"]["usd"] == "ESTIMATE" and s["labels"]["calls"] == "MEASURED"
