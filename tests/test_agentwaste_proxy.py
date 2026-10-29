from __future__ import annotations

from wattnot.agentwaste.config import WasteConfig
from wattnot.agentwaste.proxy import WasteProxy


class FakeUpstream:
    """Stand-in for GuardedClient: client(body, purpose=...) -> response."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.reply: dict = {
            "id": "r1",
            "object": "chat.completion",
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": "ok"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                      "total_tokens": 15},
        }

    def __call__(self, body: dict, purpose: str = "unspecified") -> dict:
        self.calls.append(body)
        return self.reply


def simple_body() -> dict:
    return {"model": "m", "messages": [{"role": "user", "content": "hi"}]}


def test_passthrough_and_stats() -> None:
    fake = FakeUpstream()
    proxy = WasteProxy(fake)
    out = proxy.proxy(simple_body(), purpose="test")
    assert out["choices"][0]["message"]["content"] == "ok"
    assert len(fake.calls) == 1
    stats = proxy.stats()
    assert stats["upstream_calls"] == 1
    assert stats["prompt_tokens"] == 10
    assert proxy.stopped is False
    assert proxy.stop_reason is None


def test_step_budget_stops_without_upstream() -> None:
    fake = FakeUpstream()
    proxy = WasteProxy(fake, WasteConfig(max_steps=1))
    proxy.proxy(simple_body())
    assert len(fake.calls) == 1
    out = proxy.proxy(simple_body())
    assert len(fake.calls) == 1
    assert out["id"].startswith("wattnot-stop")
    assert out["wattnot"]["stopped"] is True
    assert proxy.stopped is True
    assert proxy.stop_reason == "step budget reached"


def test_token_budget_stops_without_upstream() -> None:
    fake = FakeUpstream()
    proxy = WasteProxy(fake, WasteConfig(max_total_tokens=10))
    proxy.proxy(simple_body())
    out = proxy.proxy(simple_body())
    assert len(fake.calls) == 1
    assert out["choices"][0]["message"]["content"].startswith("Stopped early: ")
    assert proxy.stop_reason == "token budget reached"


def test_done_call_becomes_clean_stop() -> None:
    fake = FakeUpstream()
    fake.reply = {
        "id": "r2",
        "object": "chat.completion",
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": None,
                                 "tool_calls": [{
                                     "id": "d1", "type": "function",
                                     "function": {
                                         "name": "done",
                                         "arguments": '{"reason":"all set"}',
                                     }}]},
                     "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    proxy = WasteProxy(fake)
    out = proxy.proxy(simple_body())
    assert out["choices"][0]["message"]["content"] == "all set"
    assert out["wattnot"] == {"done": True, "reason": "all set"}
    assert proxy.stats()["done_called"] is True


def test_reset_starts_new_run() -> None:
    fake = FakeUpstream()
    proxy = WasteProxy(fake, WasteConfig(max_steps=1))
    proxy.proxy(simple_body())
    proxy.proxy(simple_body())
    assert proxy.stopped is True
    proxy.reset()
    assert proxy.stopped is False
    proxy.proxy(simple_body())
    assert len(fake.calls) == 2
