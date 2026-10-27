"""Second-layer detectors: tool failure storms, low information gain, paraphrase loops, and the
shared budget for helper agents."""

from __future__ import annotations

import json

from wattnot.agentwaste import SharedBudget, WasteConfig, WasteGuard
from wattnot.agentwaste.detect import (
    Call,
    Step,
    classify_result,
    find_error_storm,
    find_low_gain,
    find_paraphrase,
)


def step(name: str, args: str, result: str | None) -> Step:
    return Step(calls=[Call(name, args, result)])


def err(n: int) -> str:
    return json.dumps({"error": "HTTP 500 internal server error", "request_id": f"rq-{n}"})


def test_classify_result():
    assert classify_result(err(1)) == "error"
    assert classify_result('{"status":503,"x":1}') == "error"
    assert classify_result("Error: boom") == "error"
    assert classify_result("[]") == "empty"
    assert classify_result('{"results":[],"took_ms":9}') == "empty"
    assert classify_result('{"id":"T-1","title":"error budget"}') == "ok"
    assert classify_result('[{"id":"T-1"}]') == "ok"
    assert classify_result(None) == "ok"


def test_error_storm_fires_when_ids_change_each_time():
    steps = [step("status", f'{{"n":{i}}}', err(i)) for i in range(3)]
    flag = find_error_storm(steps, 3, 4)
    assert flag is not None and flag.kind == "error_storm" and "status" in flag.detail


def test_error_storm_near_misses():
    two = [step("status", "{}", err(i)) for i in range(2)]
    assert find_error_storm(two, 3, 4) is None  # not yet
    healed = [step("status", "{}", err(1)), step("status", "{}", err(2)),
              step("status", "{}", '{"ok":true}')]
    assert find_error_storm(healed, 3, 4) is None  # the tool recovered
    old = [step("status", "{}", err(i)) for i in range(3)] + [step("read", "{}", '{"a":1}')]
    assert find_error_storm(old, 3, 4) is None  # the agent moved on
    assert find_error_storm(two, 0, 0) is None  # off


def test_error_storm_counts_empty_answers_with_their_own_limit():
    steps = [step("search", f'{{"q":"{i}"}}', f'{{"results":[],"took_ms":{i}}}')
             for i in range(4)]
    assert find_error_storm(steps, 3, 4) is not None
    assert find_error_storm(steps[:3], 3, 4) is None


def test_low_gain_fires_on_reordered_results():
    docs = ["alpha beta gamma delta", "gamma delta alpha beta", "beta gamma delta alpha"]
    steps = [step("search", "{}", docs[i % 3] + f" score {i}") for i in range(7)]
    assert find_low_gain(steps, 5, 0.1) is not None


def test_low_gain_ignores_real_progress():
    fresh = [step("read", "{}", f"unique{chr(97 + i) * 5} words{chr(97 + i) * 4} here")
             for i in range(7)]
    assert find_low_gain(fresh, 5, 0.1) is None
    work = [step("read", "{}", "alpha beta gamma delta")] + [
        step("update", f'{{"id":{i}}}', '{"ok":true,"changed":true}') for i in range(7)]
    assert find_low_gain(work, 5, 0.1) is None  # changed: true is progress
    assert find_low_gain(work, 0, 0.1) is None  # off


def msgs(*texts: str, tool_after: bool = False) -> list[dict]:
    out: list[dict] = [{"role": "user", "content": "task"}]
    for t in texts:
        out.append({"role": "assistant", "content": t})
        out.append({"role": "user", "content": "Continue."})
    return out


def test_paraphrase_fires_on_same_answer_twice():
    m = msgs("Billing is degraded and checkout is down", "Billing is degraded, checkout is down.")
    assert find_paraphrase(m, 2) is not None


def test_paraphrase_fires_after_three_text_replies():
    m = msgs("one two three four", "completely different words appear here",
             "yet another unrelated sentence entirely")
    assert find_paraphrase(m, 2) is not None


def test_paraphrase_near_misses():
    assert find_paraphrase(msgs("Billing is degraded and checkout is down"), 2) is None
    different = msgs("billing degraded checkout down", "anything else you need today friend")
    assert find_paraphrase(different, 2) is None
    after_tool = msgs("same words same words", "same words same words")
    after_tool.append({"role": "assistant", "content": "x", "tool_calls": [{"id": "1"}]})
    assert find_paraphrase(after_tool, 2) is None  # a tool call ends the text streak
    assert find_paraphrase(msgs("same words here", "same words here"), 0) is None


def test_shared_budget_counts_calls_tokens_and_agents():
    b = SharedBudget(max_calls=3, max_tokens=100, max_agents=2)
    assert b.register_agent() and b.register_agent() and not b.register_agent()
    assert b.reason() is None
    b.charge(10)
    b.charge(10)
    assert b.reason() is None
    b.charge(10)
    assert b.reason() == "shared call budget reached"
    t = SharedBudget(max_calls=50, max_tokens=100)
    t.charge(120)
    assert t.reason() == "shared token budget reached"


def _body(n_calls: int) -> dict:
    return {"model": "m", "messages": [{"role": "user", "content": "go"}]}


def test_two_guards_share_one_budget():
    shared = SharedBudget(max_calls=2, max_tokens=10**6, max_agents=4)
    a = WasteGuard(WasteConfig.second_layer(), shared)
    b = WasteGuard(WasteConfig.second_layer(), shared)
    resp = {"usage": {"prompt_tokens": 10, "completion_tokens": 5}, "choices": [
        {"message": {"role": "assistant", "content": "hi"}}]}
    assert a.before(_body(0)).action == "go"
    a.after(resp)
    assert b.before(_body(0)).action == "go"
    b.after(resp)
    v = a.before(_body(0))
    assert v.action == "stop" and v.reason == "shared call budget reached"
    assert b.before(_body(0)).action == "stop"


def test_a_fifth_agent_is_stopped_at_once():
    shared = SharedBudget(max_calls=50, max_tokens=10**6, max_agents=2)
    guards = [WasteGuard(WasteConfig.second_layer(), shared) for _ in range(3)]
    assert guards[1].before(_body(0)).action == "go"
    v = guards[2].before(_body(0))
    assert v.action == "stop" and v.reason == "too many helper agents"


def test_guard_nudges_a_failing_tool_then_stops_it():
    cfg = WasteConfig.second_layer()
    g = WasteGuard(cfg)
    messages: list[dict] = [{"role": "user", "content": "go"}]
    for i in range(3):
        messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{i}", "type": "function",
             "function": {"name": "status", "arguments": json.dumps({"n": i})}}]})
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": err(i)})
    v = g.before({"model": "m", "messages": messages, "tools": [{"type": "function",
                                                                 "function": {"name": "status"}}]})
    assert v.action == "go" and v.flags and v.flags[0].kind == "error_storm"
    assert "blocked" in v.body["messages"][-1]["content"]
    assert any(t["function"]["name"] == "done" for t in v.body["tools"])
    again = g.before({"model": "m", "messages": messages})
    assert again.action == "stop" and again.reason == "a tool keeps failing"


def test_default_config_keeps_the_new_detectors_off():
    cfg = WasteConfig()
    assert cfg.error_limit == cfg.empty_limit == cfg.low_gain_window == cfg.paraphrase_limit == 0
    on = WasteConfig.second_layer(15)
    assert on.max_steps == 15 and on.error_limit == 3 and on.low_gain_window == 5
