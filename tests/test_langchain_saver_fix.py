"""r04e LangChain fixes: the saver keeps the task, `done` is judged as a final answer, a guard
stop still ends with an answer. Upstream replies here are SYNTHETIC."""
from fastapi.testclient import TestClient

from wattnot.agentwaste.server import create_app
from wattnot.context_saver.middleware import (ContextSaver, MemoryCompressionMiddleware,
                                              ToolDedupMiddleware)

TIERS = [{"name": "off", "model": "m", "body_extra": {"chat_template_kwargs": {"enable_thinking": False}}},
         {"name": "on", "model": "m", "reasoning": True}]
TASK = "Ann has 17 apples and buys 25 more. How many apples? Reply '#### <number>'."


def _summ(text):
    return "turn done."          # a lossy summary: no numbers, no task


_summ.calls = 0


def _convo(task=TASK, turns=6):
    m = [{"role": "system", "content": "You are a careful agent."}, {"role": "user", "content": task}]
    for i in range(turns):
        m.append({"role": "assistant", "content": f"CALL calc {{\"expr\": \"{i}+1\"}}"})
        m.append({"role": "user", "content": f"RESULT {i + 1}"})
    return m


def test_task_survives_compression():
    mw = MemoryCompressionMiddleware(summarize=_summ)
    out, st = mw.apply(_convo())
    assert st["applied"]
    users = [m["content"] for m in out if m["role"] == "user"]
    assert users[0] == TASK                       # pinned, verbatim, first user message
    assert out[0]["role"] == "system" and out[1]["content"] == TASK


def test_no_chunks_leak_between_conversations():
    mw = MemoryCompressionMiddleware(summarize=lambda t: t)   # verbatim "summaries"
    mw.apply(_convo(task="Other item: 999 widgets", turns=6))
    out, _ = mw.apply(_convo())
    assert "999" not in " ".join(str(m["content"]) for m in out)


def test_saver_end_to_end_keeps_task():
    cs = ContextSaver([ToolDedupMiddleware(), MemoryCompressionMiddleware(summarize=_summ)])
    out, _ = cs.transform(_convo(turns=8))
    assert any(m.get("content") == TASK for m in out)


def _done(reason):
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "d1", "type": "function", "function": {"name": "done",
                                                      "arguments": f"{{\"reason\": \"{reason}\"}}"}}]},
        "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def _ans(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def _client(replies):
    seen = []

    def up(body, headers):
        seen.append(body)
        return replies(body, len(seen))
    return TestClient(create_app(up, tiers=TIERS, final_check=True)), seen


def _post(c):
    return c.post("/v1/chat/completions", headers={"X-WattNot-Run": "r1", "X-WattNot-Kind": "math"},
                  json={"model": "m", "messages": [{"role": "user", "content": TASK}],
                        "tools": [{"type": "function", "function": {"name": "calculator",
                                                                    "parameters": {"type": "object"}}}]})


def test_done_without_answer_gets_finalized():
    c, seen = _client(lambda b, n: _done("Calculations completed.") if n == 1 else _ans("17+25=42\n#### 42"))
    r = _post(c).json()
    assert r["choices"][0]["message"]["content"].endswith("#### 42")
    assert len(seen) == 2 and "tools" not in seen[1]
    assert "final answer now" in seen[1]["messages"][-1]["content"]
    fc = c.get("/v1/wattnot/receipt").json()["final_check"]
    assert fc["final_answers"] == 1 and fc["tool_turns_not_judged"] == 0


def test_done_with_answer_is_kept():
    c, seen = _client(lambda b, n: _done("#### 42"))
    r = _post(c).json()
    assert len(seen) == 1 and r["choices"][0]["message"]["content"] == "#### 42"


def test_guard_stop_still_answers():
    from wattnot.agentwaste.config import WasteConfig
    seen = []

    def up(body, headers):
        seen.append(body)
        return _ans("#### 42")
    app = create_app(up, WasteConfig(max_nudges=0), tiers=TIERS, final_check=True)
    c = TestClient(app)
    loop = [{"role": "user", "content": TASK}]
    for i in range(6):
        loop += [{"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}", "type": "function",
                  "function": {"name": "calculator", "arguments": "{\"expression\": \"17+25\"}"}}]},
                 {"role": "tool", "tool_call_id": f"c{i}", "content": "42"}]
    r = c.post("/v1/chat/completions", headers={"X-WattNot-Run": "r2", "X-WattNot-Kind": "math"},
               json={"model": "m", "messages": loop}).json()
    assert r["choices"][0]["message"]["content"] == "#### 42"
    assert seen and "tools" not in seen[-1]
