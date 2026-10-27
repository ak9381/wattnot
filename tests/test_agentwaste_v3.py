"""v3: a stop carries a report, a stopped helper tells its planner, no same-subtask resend."""

from __future__ import annotations

import json

from wattnot.agentwaste import SharedBudget, WasteConfig, WasteProxy
from wattnot.agentwaste.bench import cases as C
from wattnot.agentwaste.bench.harness import run_case
from wattnot.agentwaste.detect import same_task
from wattnot.agentwaste.guard import HELPER_STOPPED, last_observation


def resp(text=None, calls=None):
    msg = {"role": "assistant", "content": text}
    if calls:
        msg["tool_calls"] = [{"id": f"c{i}-{len(json.dumps(a))}", "type": "function",
                              "function": {"name": n, "arguments": json.dumps(a)}}
                             for i, (n, a) in enumerate(calls)]
    return {"id": "x", "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}}


def case(cid):
    return next(c for c in C.all_cases() if c.id == cid)


def chat(n, result='{"content":"max_connections=120"}'):
    msgs = [{"role": "user", "content": "go"}]
    for i in range(n):
        msgs.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"t{i}", "type": "function",
             "function": {"name": "read_file", "arguments": '{"path":"/a"}'}}]})
        msgs.append({"role": "tool", "tool_call_id": f"t{i}", "content": result})
    return msgs


def test_last_observation_names_the_tool_and_trims():
    assert last_observation([{"role": "user", "content": "x"}]) is None
    tool, text = last_observation(chat(2))
    assert tool == "read_file" and "max_connections=120" in text
    assert len(last_observation(chat(1, "x " * 400))[1]) <= 220


def test_a_detector_stop_carries_the_latest_result():
    p = WasteProxy(lambda b, **k: resp("ok"), WasteConfig(max_nudges=0, repeat_limit=2))
    out = p({"messages": chat(3), "tools": [{"type": "function", "function": {"name": "x"}}]})
    text = out["choices"][0]["message"]["content"]
    assert out["wattnot"]["stopped"] and "max_connections=120" in text
    assert "Latest result (from read_file)" in text and "task was not finished" in text
    assert out["wattnot"]["last_result"]["tool"] == "read_file"


def test_a_budget_stop_does_not_claim_no_progress():
    p = WasteProxy(lambda b, **k: resp("ok"), WasteConfig(max_steps=1))
    p({"messages": chat(0)})
    out = p({"messages": chat(1)})
    text = out["choices"][0]["message"]["content"]
    assert "Nothing new" not in text and "step budget" in text


class Poller:
    """Reads the file forever; takes the done action when the saver offers it."""

    def __call__(self, body, **kw):
        return resp(calls=[("read_file", {"path": "/reports/limits.txt"})])


def test_noprog_stop_report_reaches_the_operator_and_passes_the_check():
    c = case("noprog-3-file-poll")
    on = run_case(c, Poller(), "harness+saver")
    assert on.ended_by == "saver-stop" and on.success
    assert any("max_connections=120" in s for s in on.said)
    off = run_case(c, Poller(), "harness")
    assert off.ended_by == "cap"


def test_same_task_matches_rewordings_and_not_other_work():
    assert same_task("Check the status of the billing service and post it",
                     "Check billing service status and post it to the operator")
    assert not same_task("Check the billing service status",
                         "List files under the root directories to find retention")
    assert not same_task("go", "go")


def test_shared_budget_remembers_stopped_subtasks():
    b = SharedBudget()
    assert b.find_redelegation("Search docs for audit log retention period") is None
    b.note_stopped_task("Search docs for audit log retention period", "stopped: x")
    assert b.find_redelegation("search the docs for the audit-log retention period") == "stopped: x"
    assert b.find_redelegation("read /config/app.yaml and return it") is None
    assert b.redelegations == 1


class Planner:
    """Planner delegates the same subtask over and over; a helper fails the same tool forever."""

    def __init__(self):
        self.n = 0

    def __call__(self, body, **kw):
        self.n += 1
        if "Note:" in str(body["messages"][-1].get("content")):
            return resp(calls=[("done", {"reason": "blocked: service_status keeps failing"})])
        sys = body["messages"][0]["content"]
        if "planning agent" in sys:
            last = body["messages"][-1]
            if last.get("role") == "tool" and HELPER_STOPPED in str(last.get("content")):
                return resp("Could not get the status: the helper was stopped.")
            return resp(calls=[("delegate", {"task": "Check the status of the billing service"})])
        return resp(calls=[("service_status", {"name": "billing", "i": self.n})])


def test_stopped_helper_tells_the_planner_and_the_task_is_not_sent_again():
    c = case("sprawl-2-redelegate-failure")
    on = run_case(c, Planner(), "harness+saver")
    assert on.helpers == 1 and on.ended_by == "final"
    assert any(HELPER_STOPPED in str(e["calls"]) for e in on.trace if e["agent"] == "main")


def test_second_delegation_of_a_stopped_subtask_starts_no_helper():
    class Twice(Planner):
        def __call__(self, body, **kw):
            sys = body["messages"][0]["content"]
            last = body["messages"][-1]
            if "planning agent" in sys and last.get("role") == "tool":
                n = sum(1 for m in body["messages"] if m.get("role") == "tool")
                if n < 2:
                    return resp(calls=[("delegate",
                                        {"task": "Check billing service status and post it"})])
                return resp("done")
            return super().__call__(body, **kw)

    c = case("sprawl-2-redelegate-failure")
    on = run_case(c, Twice(), "harness+saver")
    assert on.helpers == 1 and on.flags["redelegate"] == 1
    assert any("already stopped earlier" in str(e["calls"]) for e in on.trace)


def test_helper_stop_text_is_not_operator_visible_but_main_stop_text_is():
    c = case("sprawl-2-redelegate-failure")
    on = run_case(c, Planner(), "harness+saver")
    assert not any(s.startswith("Stopped early") for s in on.said)
