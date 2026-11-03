"""End to end: an agent workload through the gateway vs straight to the model, on a fake that
behaves like Muse Glimmer on vLLM (4 Nov H100 run): reasoning_effort=low makes it think MORE
(about 300 completion tokens vs 45 at high), and every tool schema costs prompt tokens.

Fails if WattNot's arm uses more tokens or energy than the direct arm once the saver gate has
had its calibration window, or more than 5% more over the whole run including calibration."""
import json

from fastapi.testclient import TestClient

from wattnot.agentwaste.server import sanitize, tier_body
from wattnot.gateway.build import build_app, load_config
from wattnot.gateway.traffic import plan

JPT = 7.86


def glimmer_like(ledger):
    def up(body, headers):
        prompt = max(1, len(json.dumps(body.get("messages", [])) + json.dumps(body.get("tools", []))) // 4)
        out = 300 if body.get("reasoning_effort") == "low" else 45
        cap = body.get("max_completion_tokens") or body.get("max_tokens")
        out = min(out, int(cap)) if cap else out
        ledger.append({"shadow": bool(headers.get("x-wattnot-shadow")), "tokens": prompt + out})
        return {"id": "x", "object": "chat.completion", "model": body["model"],
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "The H100 default limit is 700 W."}}],
                "usage": {"prompt_tokens": prompt, "completion_tokens": out}}
    return up


def run(tmp, n=60):
    cfg = load_config(None)
    cfg["savings_log"] = str(tmp / "s.jsonl")
    with_l, direct_l = [], []
    app = build_app(cfg, upstream=glimmer_like(with_l))
    app.state.monitor.sync = True
    base = next(t for t in cfg["tiers"] if t["name"] == cfg["baseline"]["tier"])
    direct = glimmer_like(direct_l)
    per_req = []
    with TestClient(app) as c:
        for i, (_, body) in enumerate(plan("agent", n)):
            k0, d0 = len(with_l), len(direct_l)
            r = c.post("/v1/chat/completions", json={"model": "glimmer", **body}, headers={"x-wattnot-run": f"e2e-{i}"})
            assert r.status_code == 200
            direct(tier_body(sanitize(body)[0], base), {})
            per_req.append((sum(x["tokens"] for x in with_l[k0:]), sum(x["tokens"] for x in direct_l[d0:])))
        gate = app.state.gate.status()
    return per_req, gate


def test_wattnot_arm_never_costs_more_than_direct_on_glimmer_agents(tmp_path):
    per_req, gate = run(tmp_path, 60)
    off = {e["saver"] for e in gate["off"]}
    assert {"routing", "agent_waste", "thinking_cap"} <= off, gate   # every saver that lost to stock switched itself off
    window = 12                                                   # calibration: 3 shadow pairs + 10 done-tool calls
    w_steady = sum(a for a, _ in per_req[window:]); d_steady = sum(b for _, b in per_req[window:])
    assert w_steady <= d_steady, (w_steady, d_steady)              # tokens, and energy = tokens x J/token
    assert w_steady * JPT <= d_steady * JPT
    w_all = sum(a for a, _ in per_req); d_all = sum(b for _, b in per_req)
    assert w_all <= d_all * 1.05, (w_all, d_all)                  # calibration included, shadows included


def test_gate_keeps_a_saver_that_is_no_worse_than_stock():
    from wattnot.gateway.gate import SaverGate
    g = SaverGate(min_pairs=3)
    for _ in range(5):
        g.record_pair("m", 400, 500, True, ["routing"])
    assert g.allow("routing", "m") and not g.status()["off"]
    g.record_pair("other", 900, 500, True, ["routing"])         # a clear loss on one model only
    assert not g.allow("routing", "other") and g.allow("routing", "m")


def test_thinking_saver_is_the_h100_holdout_setting():
    sv = load_config(None)["savers"]["thinking_cap"]
    assert sv["reasoning_effort"] == "low" and sv["max_tokens"] == 1536


def test_traffic_estimate_uses_measured_per_kind_cost():
    from wattnot.gateway.traffic import estimate
    calib = {"with": {"agent": [0.6059, 60]}, "without": {"agent": [0.2507, 60]}}
    e = estimate("agent", 60, True, calib=calib, self_hosted=True)
    assert abs(e["usd"] - (0.6059 + 0.2507) * 1.15) < 0.01 and e["api_usd"] == 0.0 and e["self_hosted"]
    assert estimate("agent", 60, True)["usd"] > 0.19          # the old under-estimate
