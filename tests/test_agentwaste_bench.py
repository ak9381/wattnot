"""Bench pieces with a fake client: the world, the task checks, the runner arms and the summary."""

from __future__ import annotations

import json

from wattnot.agentwaste.bench.env import World
from wattnot.agentwaste.bench.runner import run_all, run_task
from wattnot.agentwaste.bench.summary import summarize
from wattnot.agentwaste.bench.tasks import TASKS


def _resp(text=None, calls=None, comp=20, prompt=100):
    msg = {"role": "assistant", "content": text}
    if calls:
        msg["tool_calls"] = [{"id": f"c{i}", "type": "function",
                              "function": {"name": n, "arguments": json.dumps(a)}}
                             for i, (n, a) in enumerate(calls)]
    return {"id": "x", "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": comp,
                      "completion_tokens_details": {"reasoning_tokens": 5},
                      "prompt_tokens_details": {"cached_tokens": 10}}}


class Script:
    """Returns canned replies in order, then keeps repeating the last one."""

    def __init__(self, *replies):
        self.replies, self.n = list(replies), 0

    def __call__(self, body, **kw):
        r = self.replies[min(self.n, len(self.replies) - 1)]
        self.n += 1
        return r


def test_tasks_shape():
    assert 20 <= len(TASKS) <= 30
    assert len({t.id for t in TASKS}) == len(TASKS)
    assert {t.group for t in TASKS} == {"work", "early", "no_work"}


def test_world_is_deterministic_and_tracks_changes():
    a, b = World(), World()
    assert a.call("list_tickets", {"owner": "none"}) == b.call("list_tickets", {"owner": "none"})
    out = json.loads(a.call("update_ticket", {"id": "T-102", "owner": "dana"}))
    assert out["changed"] and len(a.mutations) == 1
    again = json.loads(a.call("update_ticket", {"id": "T-102", "owner": "dana"}))
    assert again["changed"] is False and len(a.mutations) == 1
    assert "error" in json.loads(a.call("nope", {}))


def test_no_work_task_fails_if_the_agent_invents_work():
    t = next(t for t in TASKS if t.group == "no_work")
    w = t.world()
    w.call("create_ticket", {"title": "busywork"})
    assert not t.check(w, "nothing to do")


def test_auto_arm_stops_on_text():
    t = TASKS[0]
    client = Script(_resp("Nothing to do."))
    r = run_task(t, client, "auto-ref")
    assert r.ended_by == "text" and r.calls == 1 and r.completion_tokens == 20
    assert r.cached_tokens == 10 and r.reasoning_tokens == 5


def test_loop_off_runs_to_the_cap():
    t = TASKS[0]
    client = Script(_resp("Still checking."))
    r = run_task(t, client, "loop-off", max_steps=5)
    assert r.ended_by == "cap" and r.calls == 5


def test_loop_on_ends_early_when_agent_repeats_itself():
    t = next(t for t in TASKS if t.group == "no_work")
    client = Script(_resp(calls=[("list_tickets", {"owner": "none"})]))
    r = run_task(t, client, "loop-on", max_steps=12)
    assert r.ended_by in {"stopped", "done"} and r.calls < 12 and r.stop_reason


def test_upstream_error_is_recorded_not_hidden():
    def boom(body, **kw):
        raise RuntimeError("down")
    r = run_task(TASKS[0], boom, "auto-ref")
    assert r.ended_by == "error" and "down" in r.error and not r.success


def test_run_all_and_summary_labels():
    client = Script(_resp("Nothing to do."))
    runs = run_all(client, tasks=TASKS[:3], reps=1, workers=2)
    assert len(runs) == 9
    s = summarize([r.as_dict() for r in runs])
    assert set(s["arms"]) == {"auto-ref", "loop-off", "loop-on"}
    assert s["labels"]["energy_j"] == "ESTIMATE" and s["labels"]["calls"] == "MEASURED"
    off, auto = s["arms"]["loop-off"]["all"], s["arms"]["auto-ref"]["all"]
    assert off["calls_per_run"] > auto["calls_per_run"]


def test_nothing_to_do_wording_is_accepted():
    from wattnot.agentwaste.bench.tasks import NOTHING
    for t in ("no unowned high-priority tickets to escalate", "values match, no fix needed",
              "nothing to do"):
        assert NOTHING.search(t)
    assert not NOTHING.search("assigned three tickets to dana")
