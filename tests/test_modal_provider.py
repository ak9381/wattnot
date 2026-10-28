"""Modal as a planner host, network mocked: same call record as Baseten, own provider tag."""

import json

import pytest

from wattnot.proof.modal_provider import ModalClient, ModalError, ModalOpenPlanner
from wattnot.receipt.build import planner_trace_from_events

HIST = [{"cap_w": 600, "accepted": True, "why_not": None, "energy_change_pct": -8.0,
         "p99_change_pct": 2.0, "quality_pass_pct": 93.7, "stock_pass_pct": 93.7}]


def _reply(obj):
    return {"choices": [{"message": {"content": json.dumps(obj)}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 300}}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("MODAL_ENDPOINT_URL", "https://example--llama.modal.run")
    monkeypatch.setenv("MODAL_PROXY_TOKEN_ID", "wk-test")
    monkeypatch.setenv("MODAL_PROXY_TOKEN_SECRET", "ws-test")
    monkeypatch.delenv("MODAL_MODEL", raising=False)


def test_modal_call_has_baseten_parity_fields(env):
    sent = []

    def transport(req):
        sent.append(req)
        return _reply({"kind": "try", "cap_w": 500, "reason": "halve the gap"})

    client = ModalClient(transport=transport)
    prop = ModalOpenPlanner(client).propose(HIST)
    assert sent[0]["url"] == "https://example--llama.modal.run/v1/chat/completions"
    assert "ws-test" not in json.dumps(sent[0]) and "ws-test" not in repr(client)
    assert prop["by"] == "modal-open" and prop["cap_w"] == 500
    call = prop["call"]
    assert set(call) == {"provider", "model", "prompt_sha256", "latency_ms", "label",
                         "fallback", "fallback_reason"}
    assert call["provider"] == "modal" and call["model"] == client.model
    assert client.entries[0]["provider"] == "modal"
    ev = {"type": "decision", "data": {"choice": {"kind": "cap", "power_cap_w": 500},
                                       "reason": "x", "model": client.model, "call": call}}
    assert planner_trace_from_events([ev])[0]["call"]["provider"] == "modal"


def test_missing_proxy_token_falls_back(env, monkeypatch):
    monkeypatch.delenv("MODAL_PROXY_TOKEN_SECRET")
    prop = ModalOpenPlanner(ModalClient(transport=lambda r: {})).propose(HIST)
    assert prop["by"] == "bisect-fallback" and prop["call"]["fallback"] is True
    assert prop["call"]["provider"] == "modal"


def test_missing_url_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("MODAL_ENDPOINT_URL", raising=False)
    with pytest.raises(ModalError, match="MODAL_ENDPOINT_URL"):
        ModalClient()
