"""The gateway applies the savers with the same functions and settings the H100 harness tested."""
from wattnot.agentwaste.server import create_app as harness_agent_app
from wattnot.gateway import build
from wattnot.gateway.build import build_app, load_config
from wattnot.longrun.holdout import FROZEN_STACK, apply_stack


def test_thinking_saver_matches_the_holdout_stack_exactly(tmp_path):
    cfg = load_config(None)
    cfg["savings_log"] = str(tmp_path / "s.jsonl")
    for k in cfg["savers"]:
        cfg["savers"][k]["enabled"] = k == "thinking_cap"
    cfg["baseline"]["shadow_rate"] = 0
    seen = []
    app = build_app(cfg, upstream=lambda b, h: seen.append(b) or {
        "choices": [{"message": {"role": "assistant", "content": "4"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 1}})
    from fastapi.testclient import TestClient
    msg = {"messages": [{"role": "user", "content": "2+2?"}], "max_tokens": 4000}
    with TestClient(app) as c:
        assert c.post("/v1/chat/completions", json={"model": "m", **msg}).status_code == 200
    harness = apply_stack({"model": seen[0]["model"], **msg})       # the holdout's own transform
    for k in ("reasoning_effort", "max_completion_tokens"):
        assert seen[0][k] == harness[k], k
    assert seen[0]["reasoning_effort"] == FROZEN_STACK["savers"]["reasoning_effort"] == "low"
    assert seen[0]["max_completion_tokens"] == FROZEN_STACK["max_tokens"] == 1536


def test_agent_and_routing_savers_are_the_harness_module():
    # the gateway builds its agent guard and router from the harness's agentwaste server, not a copy
    assert build.build_app.__code__.co_consts is not None
    import inspect
    src = inspect.getsource(build.build_app)
    assert "from wattnot.agentwaste.server import create_app" in src and "WasteConfig()" in src
    assert load_config(None)["savers"]["agent_waste"]["preset"] == "harness"
    assert harness_agent_app.__module__ == "wattnot.agentwaste.server"
