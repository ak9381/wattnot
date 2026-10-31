"""Proxy fixes: reasoning budgets, billed-call counting, field sanitizing, SSE streaming."""
import json

from fastapi.testclient import TestClient

from wattnot.agentwaste.server import create_app, reasoning_cutoff, sanitize, tier_body

TIERS = ({"name": "small", "model": "s", "reasoning": True, "max_output": 1500},
         {"name": "big", "model": "b"})


def _resp(content, finish="stop", comp=10, reas=0, model="s"):
    return {"id": "x", "object": "chat.completion", "created": 0, "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": finish}],
            "usage": {"prompt_tokens": 5, "completion_tokens": comp,
                      "completion_tokens_details": {"reasoning_tokens": reas}}}


class Up:
    def __init__(self, by_model):
        self.by_model, self.bodies = by_model, []

    def __call__(self, body, headers):
        self.bodies.append(body)
        return self.by_model[body["model"]]


def _client(up):
    return TestClient(create_app(up, tiers=TIERS))


def _ask(c, **extra):
    return c.post("/v1/chat/completions",
                  json={"model": "m", "messages": [{"role": "user", "content": "q?"}], **extra})


def test_reasoning_tier_gets_larger_budget():
    b = tier_body({"model": "m", "max_tokens": 400}, TIERS[0])
    assert b["max_completion_tokens"] == 1500 and "max_tokens" not in b
    assert tier_body({"model": "m", "max_tokens": 400}, TIERS[1]) == {"model": "b", "max_tokens": 400}


def test_reasoning_cutoff_not_a_failure():
    up = Up({"s": _resp("391", "length", comp=400, reas=380), "b": _resp("391", model="b")})
    r = _ask(_client(up), max_tokens=50)
    assert r.json()["model"] == "s" and len(up.bodies) == 1
    assert up.bodies[0]["max_completion_tokens"] == 1500
    assert reasoning_cutoff(_resp("", "length", 400, 380), "", "length") is False


def test_escalated_attempts_are_counted_as_billed():
    up = Up({"s": _resp("", "length", comp=400, reas=400), "b": _resp("ok answer", model="b")})
    c = _client(up)
    assert _ask(c).json()["model"] == "b"
    rc = c.get("/v1/wattnot/receipt").json()
    assert len(up.bodies) == 2
    assert rc["billed_upstream_calls"] == 2 and rc["agent_waste"]["completion_tokens"] == 410


def test_sanitize_drops_unknown_and_rejects_n():
    clean, dropped, err = sanitize({"model": "m", "messages": [], "parallel_tool_calls": True,
                                    "stream": True, "logprobs": True})
    assert err is None and dropped == ["logprobs", "parallel_tool_calls"]
    assert set(clean) == {"model", "messages"}
    assert sanitize({"n": 2})[2]
    up = Up({"s": _resp("hi there")})
    c = _client(up)
    _ask(c, parallel_tool_calls=False, stream=False)
    assert "parallel_tool_calls" not in up.bodies[0] and "stream" not in up.bodies[0]
    assert c.get("/v1/wattnot/receipt").json()["dropped_fields"] == ["parallel_tool_calls"]
    assert _ask(c, n=3).status_code == 400


def test_streaming_sse():
    up = Up({"s": _resp("hello streaming world")})
    c = _client(up)
    r = _ask(c, stream=True, stream_options={"include_usage": True})
    assert r.headers["content-type"].startswith("text/event-stream")
    lines = [ln[6:] for ln in r.text.splitlines() if ln.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    chunks = [json.loads(x) for x in lines[:-1]]
    text = "".join(ch["choices"][0]["delta"].get("content", "") for ch in chunks if ch["choices"])
    assert text == "hello streaming world"
    assert chunks[-1]["usage"]["completion_tokens"] == 10
    assert "stream" not in up.bodies[0]
    assert c.get("/v1/wattnot/receipt").json()["streamed_calls"] == 1
