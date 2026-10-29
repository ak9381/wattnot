from __future__ import annotations

from fastapi.testclient import TestClient

from wattnot.agentwaste.config import WasteConfig
from wattnot.agentwaste.server import create_app


class FakeUpstream:
    """upstream(body, headers) -> parsed response."""

    def __init__(self) -> None:
        self.calls: list[tuple[dict, dict]] = []
        self.fail = False

    def __call__(self, body: dict, headers: dict) -> dict:
        self.calls.append((body, headers))
        if self.fail:
            raise RuntimeError("boom: pretended secret leak")
        return {
            "id": "r1",
            "object": "chat.completion",
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": "ok"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                      "total_tokens": 15},
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


def make_client(max_nudges: int = 1) -> tuple[TestClient, FakeUpstream]:
    fake = FakeUpstream()
    app = create_app(fake, WasteConfig(max_nudges=max_nudges))
    return TestClient(app), fake


def test_chat_passthrough() -> None:
    client, fake = make_client()
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    resp = client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "ok"
    assert len(fake.calls) == 1


def test_nudge_then_stop_and_run_isolation() -> None:
    client, fake = make_client()
    headers_a = {"X-WattNot-Run": "run-a"}
    first = client.post("/v1/chat/completions", json=repeat_body(),
                        headers=headers_a)
    assert first.status_code == 200
    assert len(fake.calls) == 1
    sent = fake.calls[0][0]
    assert sent["messages"][-1]["role"] == "user"
    second = client.post("/v1/chat/completions", json=repeat_body(),
                         headers=headers_a)
    assert second.status_code == 200
    assert second.json()["id"].startswith("wattnot-stop")
    assert len(fake.calls) == 1
    other = client.post("/v1/chat/completions", json=repeat_body(),
                        headers={"X-WattNot-Run": "run-b"})
    assert other.status_code == 200
    assert not other.json().get("id", "").startswith("wattnot-stop")
    assert len(fake.calls) == 2


def test_default_run_id_groups_same_opener() -> None:
    client, fake = make_client()
    assert client.post("/v1/chat/completions",
                       json=repeat_body()).status_code == 200
    assert len(fake.calls) == 1
    second = client.post("/v1/chat/completions", json=repeat_body())
    assert second.json()["id"].startswith("wattnot-stop")
    assert len(fake.calls) == 1
    fresh = {"model": "m", "messages": [{"role": "user", "content": "other"}]}
    assert client.post("/v1/chat/completions", json=fresh).status_code == 200
    assert len(fake.calls) == 2


def test_streaming_rejected() -> None:
    client, _ = make_client()
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}],
            "stream": True}
    resp = client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 400
    assert "stream" in resp.json()["detail"].lower()


def test_upstream_error_is_502_without_echo() -> None:
    client, fake = make_client()
    fake.fail = True
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    headers = {"X-WattNot-Run": "run-err", "Authorization": "Bearer s3cret"}
    resp = client.post("/v1/chat/completions", json=body, headers=headers)
    assert resp.status_code == 502
    assert "boom" not in resp.text and "s3cret" not in resp.text


def test_stats_totals() -> None:
    client, fake = make_client()
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    client.post("/v1/chat/completions", json=body,
                headers={"X-WattNot-Run": "s1"})
    client.post("/v1/chat/completions", json=body,
                headers={"X-WattNot-Run": "s2"})
    resp = client.get("/v1/wattnot/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data["runs"]) == {"s1", "s2"}
    assert data["total"]["upstream_calls"] == 2
    assert data["total"]["prompt_tokens"] == 20
    assert data["runs"]["s1"]["upstream_calls"] == 1
