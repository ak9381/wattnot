from wattnot.agentwaste import WasteConfig, WasteProxy
from wattnot.context_saver import ContextSaver


def _reply(tokens=10):
    return {"choices": [{"message": {"role": "assistant", "content": "ok"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": tokens, "completion_tokens": tokens}}


def test_proxy_runs_context_saver_and_cuts_tokens_sent_upstream():
    seen = {}

    def upstream(body, **kw):
        seen["messages"] = body["messages"]
        return _reply()

    big = "File contents about the energy path. " * 60
    messages = [{"role": "system", "content": "sys"}]
    for i in range(4):
        messages.append({"role": "assistant", "content": "",
                         "tool_calls": [{"id": f"c{i}",
                                         "function": {"name": "read", "arguments": '{"p":"a"}'}}]})
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": big})
        messages.append({"role": "user", "content": f"step {i} about energy"})
    messages.append({"role": "user", "content": "final question about energy"})
    body = {"model": "m", "messages": messages, "tools": [
        {"type": "function", "function": {"name": "read", "parameters": {}}}]}

    proxy = WasteProxy(upstream, WasteConfig(max_steps=5), context_saver=ContextSaver())
    proxy.proxy(body)
    # the upstream saw fewer/smaller messages than we passed in (dedup + compression)
    from wattnot.context_saver import conversation_tokens
    assert conversation_tokens(seen["messages"]) < conversation_tokens(messages)
    cs = proxy.stats()["context_saver"]
    assert cs["tokens_saved"] > 0 and cs["label"] == "ESTIMATE"


def test_proxy_without_saver_is_unchanged():
    def upstream(body, **kw):
        return _reply()
    proxy = WasteProxy(upstream, WasteConfig(max_steps=5))
    out = proxy.proxy({"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    assert "context_saver" not in proxy.stats()
    assert out["choices"][0]["finish_reason"] == "stop"
