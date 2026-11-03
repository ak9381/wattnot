"""Side by side is fair: both arms send the same sampling params, and with every saver gated
off the WattNot arm's upstream body is byte-identical to the without arm's."""
import copy
import json

from fastapi.testclient import TestClient

from wattnot.agentwaste.server import sanitize, tier_body
from wattnot.gateway import traffic as tr
from wattnot.gateway.build import build_app, load_config


def fake(calls):
    def up(b, h):
        calls.append((dict(h), copy.deepcopy(b)))
        return {"id": "x", "object": "chat.completion", "model": b["model"],
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "700 W."}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    return up


def _bodies(tmp_path, gate_off: bool, disabled: bool):
    cfg = load_config(None)
    cfg["savings_log"] = str(tmp_path / "s.jsonl")
    if disabled:
        for k in cfg["savers"]:
            cfg["savers"][k]["enabled"] = False
    w, d = [], []
    app = build_app(cfg, upstream=fake(w))
    base = next(t for t in cfg["tiers"] if t["name"] == cfg["baseline"]["tier"])
    if gate_off:
        for s in ("routing", "thinking_cap", "agent_waste", "vision", "context_saver", "voice"):
            app.state.gate._switch_off(s, base["model"], "test")
    sent = []
    run = tr.TrafficRun(lambda b, h: sent.append(b) or {"usd": 0}, direct=lambda b: sent.append(b) or {"usd": 0})
    run.start("agent", 20, "fast", True)
    run.thread.join(5)
    body = sent[0]
    assert sent[0] == sent[1]                                   # traffic sends both arms the same body
    for k, v in tr.SAMPLING.items():
        assert body[k] == v
    with TestClient(app) as c:
        c.post("/v1/chat/completions", json=body)
    fake(d)(tier_body(sanitize(body)[0], base), {})
    a = [b for h, b in w if not h.get("x-wattnot-shadow")][0]
    return json.dumps(a, sort_keys=True), json.dumps(d[0][1], sort_keys=True)


def test_savers_disabled_bodies_byte_identical(tmp_path):
    a, b = _bodies(tmp_path, gate_off=False, disabled=True)
    assert a == b


def test_savers_gated_off_bodies_byte_identical(tmp_path):
    a, b = _bodies(tmp_path, gate_off=True, disabled=False)
    assert a == b and '"temperature": 0' in a and '"seed": 1234' in a
