from __future__ import annotations

import copy

from wattnot.agentwaste.config import WasteConfig
from wattnot.agentwaste.guard import WasteGuard


def tool_body(name: str = "read", args: str = '{"path":"a"}',
              result: str = "v1") -> dict:
    return {
        "model": "m",
        "messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": name, "arguments": args}}]},
            {"role": "tool", "tool_call_id": "c1", "content": result},
        ],
    }


def repeat_body() -> dict:
    return {
        "model": "m",
        "messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "read",
                                          "arguments": '{"path":"a"}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "same"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c2", "type": "function",
                             "function": {"name": "read",
                                          "arguments": '{"path":"a"}'}}]},
            {"role": "tool", "tool_call_id": "c2", "content": "same"},
        ],
    }


def usage_response(prompt: int = 10, completion: int = 5,
                   reasoning: int = 0) -> dict:
    return {
        "id": "r1",
        "object": "chat.completion",
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": "ok"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                  "total_tokens": prompt + completion,
                  "completion_tokens_details": {"reasoning_tokens": reasoning}},
    }


def test_nudge_then_stop_escalation() -> None:
    guard = WasteGuard(WasteConfig(max_nudges=1))
    body = repeat_body()
    snapshot = copy.deepcopy(body)
    first = guard.before(body)
    assert first.action == "go"
    assert body == snapshot
    assert len(first.body["messages"]) == len(body["messages"]) + 1
    nudge = first.body["messages"][-1]
    assert nudge["role"] == "user" and "done" in nudge["content"].lower()
    assert guard.stats()["nudges"] == 1
    second = guard.before(body)
    assert second.action == "stop"
    assert second.reason == "repeated calls"
    assert [f.kind for f in second.flags] == ["repeat"]
    assert guard.stats()["stopped"] is True


def test_no_op_stop_reason() -> None:
    guard = WasteGuard(WasteConfig(max_nudges=0, noop_limit=2))
    body = {
        "model": "m",
        "messages": [{"role": "user", "content": "go"}],
    }
    for i, path in enumerate(["a", "b", "c"]):
        body["messages"] += [
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": f"c{i}", "type": "function",
                             "function": {"name": "read",
                                          "arguments": f'{{"path":"{path}"}}'}}]},
            {"role": "tool", "tool_call_id": f"c{i}", "content": "same"},
        ]
    verdict = guard.before(body)
    assert verdict.action == "stop"
    assert verdict.reason == "no new information"


def test_step_budget_stops() -> None:
    guard = WasteGuard(WasteConfig(max_steps=1))
    guard.after(usage_response())
    verdict = guard.before(tool_body())
    assert verdict.action == "stop"
    assert verdict.reason == "step budget reached"
    assert verdict.body is None


def test_token_budget_stops() -> None:
    guard = WasteGuard(WasteConfig(max_total_tokens=10))
    guard.after(usage_response(prompt=6, completion=5))
    verdict = guard.before(tool_body())
    assert verdict.action == "stop"
    assert verdict.reason == "token budget reached"


def test_done_tool_injected_only_when_tools_exist() -> None:
    guard = WasteGuard()
    body = tool_body()
    body["tools"] = [{"type": "function",
                      "function": {"name": "read",
                                   "description": "r",
                                   "parameters": {"type": "object"}}}]
    snapshot = copy.deepcopy(body)
    verdict = guard.before(body)
    assert verdict.action == "go"
    assert body == snapshot
    names = [t["function"]["name"] for t in verdict.body["tools"]]
    assert names == ["read", "done"]
    done = verdict.body["tools"][1]["function"]
    assert done["parameters"]["required"] == ["reason"]

    plain = tool_body()
    assert guard.before(plain).body.get("tools") is None

    again = guard.before(verdict.body)
    names = [t["function"]["name"] for t in again.body["tools"]]
    assert names.count("done") == 1


def test_done_call_becomes_clean_stop() -> None:
    guard = WasteGuard()
    response = usage_response()
    response["choices"][0]["message"] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "d1", "type": "function",
                        "function": {"name": "done",
                                     "arguments": '{"reason":"all set"}'}}],
    }
    out = guard.after(response)
    assert response["choices"][0]["message"]["tool_calls"]
    first = out["choices"][0]
    assert first["message"]["content"] == "all set"
    assert "tool_calls" not in first["message"]
    assert first["finish_reason"] == "stop"
    assert out["wattnot"] == {"done": True, "reason": "all set"}
    stats = guard.stats()
    assert stats["done_called"] is True


def test_reasoning_overspend_lowers_effort_and_caps() -> None:
    guard = WasteGuard()
    for _ in range(2):
        guard.after(usage_response(reasoning=100))
    guard.after(usage_response(reasoning=900))
    body = tool_body()
    body["reasoning_effort"] = "high"
    body["max_tokens"] = 4000
    verdict = guard.before(body)
    assert verdict.action == "go"
    assert verdict.body["reasoning_effort"] == "medium"
    assert verdict.body["max_tokens"] == 2000
    assert [f.kind for f in verdict.flags] == ["reasoning"]
    assert body["reasoning_effort"] == "high"


def test_reasoning_overspend_never_stops_alone() -> None:
    guard = WasteGuard(WasteConfig(max_nudges=0))
    for _ in range(2):
        guard.after(usage_response(reasoning=100))
    guard.after(usage_response(reasoning=900))
    verdict = guard.before(tool_body())
    assert verdict.action == "go"


def test_reasoning_effort_steps_down() -> None:
    for effort, expected in [("medium", "low"), ("low", "minimal")]:
        guard = WasteGuard()
        for _ in range(2):
            guard.after(usage_response(reasoning=100))
        guard.after(usage_response(reasoning=900))
        body = tool_body()
        body["reasoning_effort"] = effort
        verdict = guard.before(body)
        assert verdict.body["reasoning_effort"] == expected


def test_stop_response_shape() -> None:
    guard = WasteGuard()
    out = guard.stop_response("repeated calls", model="m")
    assert out["id"] == "wattnot-stop"
    assert out["object"] == "chat.completion"
    assert out["choices"][0]["message"]["content"] == (
        "Stopped early: repeated calls. Nothing new came back, so the task was not finished.")
    assert out["choices"][0]["finish_reason"] == "stop"
    assert out["usage"] == {"prompt_tokens": 0, "completion_tokens": 0,
                            "total_tokens": 0}
    assert out["wattnot"] == {"stopped": True, "reason": "repeated calls"}


def test_stats_counts() -> None:
    guard = WasteGuard()
    guard.after(usage_response(prompt=7, completion=3, reasoning=4))
    stats = guard.stats()
    assert stats["upstream_calls"] == 1
    assert stats["prompt_tokens"] == 7
    assert stats["completion_tokens"] == 3
    assert stats["reasoning_tokens"] == 4
    assert stats["stopped"] is False
    assert stats["stop_reason"] is None


def test_config_from_dict_rejects_unknown() -> None:
    import pytest

    with pytest.raises(ValueError):
        WasteConfig.from_dict({"max_steps": 3, "bogus": 1})
    assert WasteConfig.from_dict({"max_steps": 3}).max_steps == 3
