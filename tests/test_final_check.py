"""Final-answer router (r04c LangChain fix). Upstream replies here are SYNTHETIC."""
from fastapi.testclient import TestClient

from wattnot.agentwaste.server import create_app
from wattnot.route import final_check as fc

TIERS = [{"name": "off", "model": "m", "body_extra": {"chat_template_kwargs": {"enable_thinking": False}}},
         {"name": "on", "model": "m", "reasoning": True}]


def _resp(text=None, tool=False):
    msg = {"role": "assistant", "content": text}
    if tool:
        msg["tool_calls"] = [{"id": "t1", "type": "function",
                              "function": {"name": "calculator", "arguments": "{\"expression\": \"2+2\"}"}}]
    return {"choices": [{"message": msg, "finish_reason": "tool_calls" if tool else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def test_math_checks():
    m = [{"role": "user", "content": "What is 2+2?"}]
    assert fc.check("math", "so\n#### 4", "stop", m)[0]
    assert not fc.check("math", "The result is four.", "stop", m)[0]
    assert not fc.check("math", "#### maybe 4", "stop", m)[0]
    assert not fc.check("math", "", "stop", m)[0]
    assert not fc.check("math", "#### 4", "length", m)[0]


def test_calculator_disagreement():
    m = [{"role": "user", "content": "What is 6*7?"},
         {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "calculator"}}]},
         {"role": "tool", "tool_call_id": "c1", "content": "42"}]
    assert fc.check("math", "#### 42", "stop", m)[0]
    ok, why = fc.check("math", "#### 41", "stop", m)
    assert not ok and why.startswith("disagreement")


def test_code_and_logprob():
    m = [{"role": "user", "content": "Write a python function"}]
    assert fc.check("code", "```python\ndef f(x):\n    return x\n```", "stop", m)[0]
    assert not fc.check("code", "```python\ndef f(x) return\n```", "stop", m)[0]
    low = {"choices": [{"logprobs": {"content": [{"logprob": -2.0}, {"logprob": -1.5}]}}]}
    assert not fc.check("math", "#### 4", "stop", [], low)[0]
    assert fc.infer_kind(m) == "code" and fc.infer_kind([{"role": "user", "content": "3 apples"}]) == "math"


def _client(replies, **kw):
    seen = []

    def up(body, headers):
        seen.append(body)
        return replies(body, len(seen))
    app = create_app(up, tiers=TIERS, final_check=True, **kw)
    return TestClient(app), seen


def _post(c, run="r1", msgs=None):
    return c.post("/v1/chat/completions", headers={"X-WattNot-Run": run, "X-WattNot-Kind": "math"},
                  json={"model": "m", "messages": msgs or [{"role": "user", "content": "What is 2+2?"}]})


def test_tool_turn_not_judged_and_not_stepped_up():
    c, seen = _client(lambda b, n: _resp(tool=True))
    _post(c)
    assert len(seen) == 1 and seen[0]["chat_template_kwargs"]["enable_thinking"] is False
    rc = c.get("/v1/wattnot/receipt").json()
    assert rc["final_check"]["tool_turns_not_judged"] == 1 and rc["final_check"]["final_answers"] == 0


def test_shaky_final_steps_up_once_then_cap():
    def replies(b, n):
        on = "max_completion_tokens" in b
        return _resp("#### 4" if on else "I think it is four")
    c, seen = _client(replies, max_stepups=1)
    r = _post(c).json()
    assert "#### 4" in r["choices"][0]["message"]["content"] and len(seen) == 2
    _post(c)   # same run: cap spent, the thinking-off answer is kept
    assert len(seen) == 3
    _post(c, run="r2")   # new run gets its own step-up
    assert len(seen) == 5
    rc = c.get("/v1/wattnot/receipt").json()
    assert rc["final_check"]["stepped_up"] == 2


def test_solid_final_stays_thinking_off():
    c, seen = _client(lambda b, n: _resp("Adding them.\n#### 4"))
    _post(c)
    assert len(seen) == 1


def test_r04c_pattern_synthetic():
    """SYNTHETIC replay of the r04c shape: 20 agent runs, think-off finals that are unparseable or
    hedged on the items r04c lost; every such run steps up exactly once and ends on thinking on."""
    lost = {0, 1, 5, 8, 9, 13, 17, 19}   # r04c saver_on misses not also missed by saver_off (MEASURED ids)
    def replies(b, n):
        on = "max_completion_tokens" in b
        run = b["messages"][0]["content"]
        bad = int(run.split()[1]) in lost
        return _resp("#### 7" if on or not bad else "The answer is probably around seven")
    c, seen = _client(replies)
    for i in range(20):
        _post(c, run=f"lc-{i}", msgs=[{"role": "user", "content": f"item {i} what is 3+4?"}])
    rc = c.get("/v1/wattnot/receipt").json()
    assert rc["final_check"]["stepped_up"] == len(lost) and len(seen) == 20 + len(lost)


def test_flag_off_keeps_old_gate():
    seen = []
    app = create_app(lambda b, h: (seen.append(b), _resp(tool=True))[1], tiers=TIERS)
    TestClient(app).post("/v1/chat/completions", json={"model": "m", "messages": [{"role": "user", "content": "x"}]})
    assert len(seen) == 1
