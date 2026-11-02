"""Short-conversation bypass (after r04f-langchain). Upstream replies here are SYNTHETIC."""
from fastapi.testclient import TestClient

from wattnot.agentwaste.server import create_app
from wattnot.context_saver.middleware import ContextSaver

TIERS = [{"name": "off", "model": "m"}, {"name": "on", "model": "m", "reasoning": True}]
TOOLS = [{"type": "function", "function": {"name": "calc", "description": "math",
                                            "parameters": {"type": "object", "properties": {}}}}]


def _app(reply, **kw):
    seen = []

    def up(body, headers):
        seen.append(body)
        return reply(body)
    app = create_app(up, context_saver=ContextSaver(), tiers=TIERS, final_check=True, **kw)
    return TestClient(app), seen


def _msg(text):
    return lambda b: {"choices": [{"message": {"role": "assistant", "content": text},
                                   "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}


def _post(c, content):
    return c.post("/v1/chat/completions", json={"model": "m", "tools": TOOLS,
                                               "messages": [{"role": "user", "content": content}]})


def test_short_call_gets_no_done_tool_and_no_check():
    c, seen = _app(_msg("I think it is about five"))          # shaky answer, no '####'
    r = _post(c, "What is 2+3?")
    assert r.status_code == 200
    assert len(seen) == 1                                      # no step-up, no finalize
    names = [t["function"]["name"] for t in seen[0]["tools"]]
    assert names == ["calc"]                                   # done not injected
    assert "####" not in seen[0]["tools"][0]["function"].get("description", "")
    assert c.get("/v1/wattnot/receipt").json()["final_check"]["stepped_up"] == 0


def test_short_empty_reply_still_checked():
    c, seen = _app(_msg(""))
    _post(c, "What is 2+3?")
    assert len(seen) == 2                                      # empty final answer steps up


def test_long_call_unchanged():
    c, seen = _app(_msg("I think it is about five"))
    _post(c, "context " * 3000 + " What is 2+3?")
    names = [t["function"]["name"] for t in seen[0]["tools"]]
    assert "done" in names
    assert len(seen) == 2                                      # shaky final answer stepped up


def test_bypass_off_restores_old_behaviour():
    c, seen = _app(_msg("I think it is about five"), short_bypass_tokens=0)
    _post(c, "What is 2+3?")
    assert "done" in [t["function"]["name"] for t in seen[0]["tools"]]
    assert len(seen) == 2


def test_custom_threshold():
    c, seen = _app(_msg("I think it is about five"), short_bypass_tokens=5)
    _post(c, "What is two plus three, please answer carefully?")
    assert "done" in [t["function"]["name"] for t in seen[0]["tools"]]
