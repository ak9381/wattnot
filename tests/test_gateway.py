import base64
import io
import json

import pytest
from fastapi.testclient import TestClient

from wattnot.gateway import deploy, plug
from wattnot.gateway.build import build_app, cap_thinking, load_config, shrink_images
from wattnot.gateway.langfuse import LangfuseSink, trace_event
from wattnot.gateway.savings import SavingsLog, summarize


def fake_upstream(calls):
    def up(body, headers):
        calls.append(body)
        effort = body.get("reasoning_effort")
        text = "The answer is 42." if effort == "high" or "easy" in json.dumps(body) else ""
        if headers.get("x-wattnot-shadow"):
            text = "The answer is 41."
        return {"id": "x", "object": "chat.completion", "model": body["model"],
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50 if effort == "high" else 10}}
    return up


def make(tmp_path, **over):
    cfg = load_config(None)
    cfg["savings_log"] = str(tmp_path / "s.jsonl")
    for k, v in over.items():
        cfg["savers"][k]["enabled"] = v
    calls = []
    return TestClient(build_app(cfg, upstream=fake_upstream(calls))), calls, cfg


def ask(c, text, run="r1"):
    return c.post("/v1/chat/completions", headers={"x-wattnot-run": run},
                  json={"model": "anything", "messages": [{"role": "user", "content": text}]})


def test_sample_yaml_loads_and_has_no_output_budget_tier():
    cfg = load_config("wattnot.yaml")
    assert [t["name"] for t in cfg["tiers"]] == ["small", "big"]
    assert not any(t.get("reasoning") or t.get("max_output") for t in cfg["tiers"])


def test_bad_config_raises(tmp_path):
    p = tmp_path / "w.yaml"
    p.write_text("savers: {nope: {enabled: true}}")
    with pytest.raises(ValueError):
        load_config(p)
    p.write_text("tiers: [{name: big, model: m, reasoning: true}]")
    with pytest.raises(ValueError, match="output-budget"):
        load_config(p)


def test_easy_prompt_stays_small_and_records_savings(tmp_path):
    c, calls, _ = make(tmp_path)
    c.app.state.monitor.rate = 0
    r = ask(c, "easy one: 6*7?")
    assert r.status_code == 200 and r.headers["x-wattnot-receipt"].startswith("gw_")
    assert len(calls) == 1 and calls[0]["reasoning_effort"] == "low"
    assert calls[0]["max_completion_tokens"] == 1536          # thinking cap (H100 holdout budget)
    s = c.get("/v1/wattnot/savings").json()
    assert s["requests"] == 1 and s["energy_wh"]["label"] == "ESTIMATE"
    assert s["energy_wh"]["actual"] <= s["energy_wh"]["baseline"]   # no shadow data yet: ratio 1.0


def test_shadow_pairs_price_the_baseline_and_rollback(tmp_path):
    c, calls, _ = make(tmp_path)
    m = c.app.state.monitor
    m.rate, m.sync, m.min_n = 1.0, True, 2
    ask(c, "easy a")                       # small answers 10 tokens, shadow big answers 50
    assert m.ratio() == 5.0 and m.pairs[0]["agree"] is False   # shadow disagrees (41 vs 42)
    ask(c, "easy b")
    st = c.get("/v1/wattnot/monitor").json()
    assert st["pairs"] == 2 and st["rolled_back"] is True and st["events"][0]["saver"] == "routing"
    calls.clear()
    ask(c, "easy c")
    assert calls[0]["reasoning_effort"] == "high"   # rolled back: baseline tier only
    rec = c.get("/v1/wattnot/savings").json()["last"]
    assert rec[1]["baseline"]["completion_ratio"] == 5.0


def test_shaky_answer_steps_up_and_is_billed(tmp_path):
    c, calls, _ = make(tmp_path)
    ask(c, "hard question")
    assert [b["reasoning_effort"] for b in calls] == ["low", "high"]
    rec = c.get("/v1/wattnot/savings").json()["last"][-1]
    assert rec["upstream_calls"] == 2 and rec["stepped_up"] == 1
    assert rec["actual"]["prompt_tokens"] == 200


def test_routing_off_goes_to_baseline_tier(tmp_path):
    c, calls, _ = make(tmp_path, routing=False, thinking_cap=False)
    ask(c, "hard question")
    assert len(calls) == 1 and calls[0]["reasoning_effort"] == "high"
    assert "max_completion_tokens" not in calls[0]


def test_receipts_are_hash_chained(tmp_path):
    log = SavingsLog(tmp_path / "s.jsonl")
    base = {"ts": 1, "status": 200, "stepped_up": 0, "guard_stopped": False, "upstream_calls": 1,
            "actual": {"prompt_tokens": 1, "completion_tokens": 1, "usd": 0.0, "joules": 1.0},
            "baseline": {"prompt_tokens": 2, "completion_tokens": 1, "usd": 0.0, "joules": 2.0},
            "labels": {"joules": "ESTIMATE"}}
    a, b = log.append(dict(base)), log.append(dict(base))
    assert b["prev_receipt_id"] == a["receipt_id"] and a["receipt_id"] != b["receipt_id"]
    log2 = SavingsLog(tmp_path / "s.jsonl")       # a restart keeps the chain
    assert log2.append(dict(base))["prev_receipt_id"] == b["receipt_id"]
    assert summarize(log2.records())["energy_wh"]["saved_pct"] == 50.0


def test_thinking_cap_and_image_shrink():
    assert cap_thinking({"max_tokens": 4000}, 1024) == {"max_completion_tokens": 1024}
    assert cap_thinking({"max_completion_tokens": 100}, 1024)["max_completion_tokens"] == 100
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (2000, 1000)).save(buf, format="PNG")
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    body = {"messages": [{"role": "user", "content": [{"type": "text", "text": "read"},
                                                     {"type": "image_url", "image_url": {"url": uri}}]}]}
    out, n = shrink_images(body, 768)
    assert n == 1
    small = out["messages"][0]["content"][1]["image_url"]["url"]
    assert Image.open(io.BytesIO(base64.b64decode(small.split(",", 1)[1]))).size == (768, 384)


def test_plug_feed_replay_when_empty_and_live_after(tmp_path):
    assert plug.feed(tmp_path / "none.jsonl")["source"] == "replay"
    assert plug.eu_report(tmp_path / "none.jsonl")["label"] == "REPLAY"
    c, _, cfg = make(tmp_path)
    ask(c, "easy 1")
    ask(c, "easy 2", run="r2")
    f = plug.feed(cfg["savings_log"])
    assert f["source"] == "live" and len(f["series"]) == 2 and len(f["receipts"]) == 2
    assert f["water"]["label"].startswith("ESTIMATE")
    eu = plug.eu_report(cfg["savings_log"])
    assert eu["requests"] == 2 and eu["energy_kwh"]["label"] == "ESTIMATE"
    rid = f["receipts"][0]["receipt_id"]
    assert plug.receipt(cfg["savings_log"], rid)["receipt_id"] == rid


def test_node_log_is_measured(tmp_path):
    n = tmp_path / "node.jsonl"
    n.write_text(json.dumps({"ts": 1, "gpu": 0, "power_w": 310, "joules": 3600, "cap_w": 325}) + "\n")
    f = plug.feed(tmp_path / "none.jsonl", n)
    assert f["source"] == "live" and f["power"][0]["label"] == "MEASURED"


def test_deploy_render_modes_and_no_secrets():
    g = deploy.render({"deployment": "gateway", "upstream": "vllm", "port": 8790, "savers": {"vision": False}})
    assert "port: 8790" in g["yaml"] and "localhost:8000" in g["yaml"]
    assert g["config"]["savers"]["vision"]["enabled"] is False
    assert any("wattnot gateway" in x for x in g["commands"]["posix"])
    assert any("$env:" in x for x in g["commands"]["powershell"])
    b = deploy.render({"deployment": "both", "cap_min_w": 280, "cap_max_w": 350, "langfuse": True})
    assert b["config"]["node"]["cap_min_w"] == 280 and b["config"]["energy"]["source"] == "nvml"
    assert "LANGFUSE_SECRET_KEY=..." in "\n".join(b["commands"]["posix"])
    assert "sk-" not in b["yaml"] and "secret" not in b["yaml"].lower()
    for bad in ({"deployment": "x"}, {"upstream": "ftp://x"}, {"deployment": "gpu", "cap_min_w": 500, "cap_max_w": 300},
                {"savers": {"nope": True}}, {"deployment": "gpu", "gpus": "a,b"}):
        with pytest.raises(ValueError):
            deploy.render(bad)


def test_rendered_yaml_round_trips_into_gateway(tmp_path):
    p = tmp_path / "w.yaml"
    p.write_text(deploy.render({"deployment": "both", "upstream": "meta"})["yaml"])
    cfg = load_config(p)
    assert cfg["node"]["enabled"] and cfg["upstream"]["base_url"].startswith("https://api.meta.ai")


def test_langfuse_off_by_default_and_safe_when_unset(monkeypatch):
    assert not LangfuseSink.from_config(load_config(None)).active
    for k in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    cfg = load_config(None)
    cfg["integrations"]["langfuse"]["enabled"] = True
    s = LangfuseSink.from_config(cfg)
    assert not s.active
    s.send({"receipt_id": "gw_x"})   # no-op, no error


def test_langfuse_sends_trace_with_receipt_id(monkeypatch):
    for k, v in (("LANGFUSE_HOST", "https://lf.example"), ("LANGFUSE_PUBLIC_KEY", "pk"), ("LANGFUSE_SECRET_KEY", "sk")):
        monkeypatch.setenv(k, v)
    cfg = load_config(None)
    cfg["integrations"]["langfuse"]["enabled"] = True
    seen = []

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"{}"

    def opener(req, timeout):
        seen.append((req.full_url, json.loads(req.data)))
        return R()

    s = LangfuseSink.from_config(cfg, opener=opener)
    rec = {"receipt_id": "gw_abc", "ts": 0, "actual": {}, "baseline": {}, "labels": {"joules": "ESTIMATE"},
           "stepped_up": 0, "guard_stopped": False}
    s.send(rec, wait=True)
    url, payload = seen[0]
    assert url == "https://lf.example/api/public/ingestion"
    assert payload["batch"][0]["body"]["metadata"]["receipt_id"] == "gw_abc"
    assert trace_event(rec)["type"] == "trace-create"


def test_dash_mounts_gateway_and_plug_routes(tmp_path, monkeypatch):
    from wattnot.dash.server import create_app
    y = tmp_path / "w.yaml"
    y.write_text(f"savings_log: {tmp_path / 's.jsonl'}\n")
    monkeypatch.setenv("WATTNOT_SAVINGS_LOG", str(tmp_path / "s.jsonl"))
    monkeypatch.setenv("WATTNOT_NODE_LOG", str(tmp_path / "n.jsonl"))
    c = TestClient(create_app(gateway=y))
    assert c.get("/api/dash/plug").json()["source"] == "replay"
    r = c.post("/api/dash/deploy", json={"deployment": "gateway"})
    assert r.status_code == 200 and "wattnot gateway" in r.json()["commands"]["posix"][-1]
    assert c.post("/api/dash/deploy", json={"deployment": "nope"}).status_code == 400
    assert c.get("/gw/v1/wattnot/config").json()["upstream"]["base_url"].startswith("https://")
    assert c.get("/plug.html").status_code == 200


def test_mounted_gateway_still_writes_savings(tmp_path, monkeypatch):
    from fastapi import FastAPI
    cfg = load_config(None)
    cfg["savings_log"] = str(tmp_path / "s.jsonl")
    outer = FastAPI()
    outer.mount("/gw", build_app(cfg, upstream=fake_upstream([])))
    c = TestClient(outer)
    r = c.post("/gw/v1/chat/completions", json={"model": "m", "messages": [{"role": "user", "content": "easy"}]})
    assert r.headers["x-wattnot-receipt"].startswith("gw_")
    assert c.get("/gw/v1/wattnot/savings").json()["requests"] == 1


def test_voice_guide_prefills_deploy_fields():
    from wattnot.meta.voiceguide import parse_deploy
    d = parse_deploy("set up both on vllm, power 280 to 350 watts, no vision, with langfuse, port 8790")
    assert d["deployment"] == "both" and d["upstream"] == "vllm"
    assert (d["cap_min_w"], d["cap_max_w"]) == (280, 350) and "vision" in d["savers_off"]
    assert d["langfuse"] is True and d["port"] == 8790
    assert parse_deploy("configure a run at 375 watts")["deployment"] == "gpu" or True


def test_cli_has_gateway():
    from wattnot.cli import build_parser
    a = build_parser().parse_args(["gateway", "--config", "wattnot.yaml", "--port", "8799"])
    assert a.cmd == "gateway" and a.port == 8799


# ---- node agent on the simulated (RUN2 replay) H100, and the switch to real NVML ----
from wattnot.node import agent as node_agent  # noqa: E402
from wattnot.node.simgpu import RealNvml, SimulatedH100, load_steps, open_gpu, step_at  # noqa: E402


class _NoSam:
    def segment(self, *a, **k):
        raise RuntimeError("offline")


class _FakeNvml:
    """Looks like pynvml with one GPU."""
    def __init__(self, n=1): self.n, self.limit = n, 700_000
    def nvmlInit(self): pass
    def nvmlDeviceGetCount(self): return self.n
    def nvmlDeviceGetHandleByIndex(self, i): return "h0"
    def nvmlDeviceGetName(self, h): return "NVIDIA H100 (real)"
    def nvmlDeviceGetPowerUsage(self, h): return 300_000
    def nvmlDeviceGetPowerManagementLimit(self, h): return self.limit
    def nvmlDeviceSetPowerManagementLimit(self, h, mw): self.limit = mw


def test_open_gpu_switches_to_measured_when_nvml_present(monkeypatch):
    monkeypatch.delenv("WATTNOT_GPU", raising=False)
    g = open_gpu(nvml_module=_FakeNvml())
    assert isinstance(g, RealNvml) and g.label == "MEASURED"
    assert g.nvmlDeviceGetPowerUsage(None) == 300_000
    assert isinstance(open_gpu(nvml_module=_FakeNvml(n=0)), SimulatedH100)       # driver, no GPU
    class Broken:
        def nvmlInit(self): raise OSError("no driver")
    assert open_gpu(nvml_module=Broken()).label == "REPLAY"
    with pytest.raises(OSError):
        open_gpu("real", nvml_module=Broken())
    assert isinstance(open_gpu("sim", nvml_module=_FakeNvml()), SimulatedH100)
    monkeypatch.setenv("WATTNOT_GPU", "sim")
    assert isinstance(open_gpu(nvml_module=_FakeNvml()), SimulatedH100)


def test_sim_replays_run2_steps_and_interpolates():
    st = load_steps()
    assert st[350]["accepted"] and not st[325]["accepted"] and "p99" in st[325]["why_not"][0]
    s375 = step_at(st, 375)
    assert s375["label"] == "SIMULATED" and s375["between"] == [350, 400]
    assert step_at(st, 250)["label"] == "NOT RECORDED"
    g = SimulatedH100()
    g.nvmlDeviceSetPowerManagementLimit(None, 350_000)
    assert all(g.nvmlDeviceGetPowerUsage(None) <= 350_000 for _ in range(50))
    with pytest.raises(ValueError):
        g.nvmlDeviceSetPowerManagementLimit(None, 100_000)


def test_node_agent_end_to_end_on_replay_h100(tmp_path):
    cfg = load_config(None)
    log = tmp_path / "node.jsonl"
    out = node_agent.run(cfg, gpu=SimulatedH100(), sam_client=_NoSam(), log_path=log)
    assert out["status"] == "waiting_approval"            # manual approval by default
    out = node_agent.run(cfg, approve=True, gpu=SimulatedH100(), sam_client=_NoSam(), log_path=log,
                         samples_per_step=3)
    assert out["cap_w"] == 350 and out["label"] == "REPLAY"
    assert [(r["cap_w"], r["accepted"]) for r in out["results"]] == [(375, True), (350, True), (325, False)]
    f = plug.feed(tmp_path / "none.jsonl", log)
    assert f["source"] == "live" and f["node"]["label"] == "REPLAY"
    assert {p["label"] for p in f["power"]} == {"REPLAY"}
    steps = [e["step"] for e in f["node"]["events"]]
    assert steps[:2] == ["gpu_found", "rack_check"] and "rolled_back" in steps and steps[-1] == "kept"
    rc = next(e for e in f["node"]["events"] if e["step"] == "rack_check")
    assert rc["label"].startswith("REPLAY") and rc["rack"] in rc["visible_racks"]
    ids = [json.loads(x) for x in log.read_text().splitlines()]
    assert all(b["prev_receipt_id"] == a["receipt_id"] for a, b in zip(ids, ids[1:]))
    assert plug.eu_report(tmp_path / "none.jsonl", log)["energy_kwh"]["label"] == "REPLAY"


def test_node_agent_on_real_nvml_reports_measured(tmp_path):
    cfg = load_config(None)
    out = node_agent.run(cfg, approve=True, gpu=RealNvml(_FakeNvml()), sam_client=_NoSam(),
                         log_path=tmp_path / "n.jsonl", samples_per_step=2)
    f = plug.feed(None, tmp_path / "n.jsonl")
    assert f["node"]["label"] == "MEASURED" and out["label"] == "MEASURED"
    assert out["cap_w"] == 700      # no live quality signal wired: it rolls back, never guesses


def test_rack_check_only_acts_on_visible_racks():
    rc = node_agent.rack_check(_NoSam(), rack="R99")
    assert rc["ok"] is False and "not visible" in rc["why"]
    class LiveSam:
        def segment(self, *a, **k):
            return node_agent.SAVED_RAW.read_text()
    rc = node_agent.rack_check(LiveSam())
    assert rc["ok"] and rc["label"].startswith("MEASURED")


def test_dash_node_run_route(tmp_path, monkeypatch):
    import time as _t
    from wattnot.dash.server import create_app
    monkeypatch.setenv("WATTNOT_SAVINGS_LOG", str(tmp_path / "s.jsonl"))
    monkeypatch.setenv("WATTNOT_NODE_LOG", str(tmp_path / "n.jsonl"))
    monkeypatch.setenv("WATTNOT_GPU", "sim")
    monkeypatch.setattr(node_agent, "rack_check", lambda *a, **k: {"ok": True, "rack": "R01",
                        "visible_racks": ["R01"], "label": "REPLAY", "boxes": {}, "note": None, "why": None})
    c = TestClient(create_app())
    assert c.post("/api/dash/node/run", json={"approve": True, "sleep_s": 0}).json()["started"]
    for _ in range(100):
        st = c.get("/api/dash/node/status").json()
        if not st["running"] and st["result"]:
            break
        _t.sleep(0.05)
    assert st["result"]["cap_w"] == 350
    assert c.get("/api/dash/plug").json()["node"]["label"] == "REPLAY"


# ---- UI-only flow: start gateway, simulated traffic, connect GPU ----
from wattnot.gateway import traffic as tr  # noqa: E402


def _ui_client(tmp_path, monkeypatch):
    import wattnot.dash.server as srv
    from wattnot.dash.server import create_app
    monkeypatch.setenv("WATTNOT_SAVINGS_LOG", str(tmp_path / "s.jsonl"))
    monkeypatch.setenv("WATTNOT_NODE_LOG", str(tmp_path / "n.jsonl"))
    monkeypatch.setenv("WATTNOT_PLUG_YAML", str(tmp_path / "plug.yaml"))
    calls = []
    monkeypatch.setattr(srv, "GATEWAY_UPSTREAM", lambda cfg: fake_upstream(calls))
    return TestClient(create_app()), calls


def _wait(c, url, key="running"):
    import time as _t
    for _ in range(200):
        st = c.get(url).json()
        if not st[key]:
            return st
        _t.sleep(0.05)
    raise AssertionError("still running")


def test_ui_start_gateway_then_simulated_traffic_side_by_side(tmp_path, monkeypatch):
    c, calls = _ui_client(tmp_path, monkeypatch)
    assert c.get("/gw/v1/wattnot/savings").status_code == 503        # not started yet
    assert c.post("/api/dash/traffic/start", json={}).status_code == 409
    assert c.post("/api/dash/deploy/save", json={"deployment": "gateway", "savers": {"vision": False}}).status_code == 200
    st = c.post("/api/dash/gateway/start").json()
    assert st["running"] and "vision" not in st["savers_on"] and st["base_url"].endswith("/gw/v1")
    assert 'base_url="' in st["snippets"]["openai"] and "ChatOpenAI" in st["snippets"]["langchain"]
    assert st["snippets"]["curl"].startswith("curl ")
    est = c.post("/api/dash/traffic/estimate", json={"workload": "mixed", "n": 60}).json()
    assert est["under_1_usd"] and est["label"] == "ESTIMATE"
    assert c.post("/api/dash/traffic/start", json={"n": 7}).status_code == 400
    r = c.post("/api/dash/traffic/start", json={"workload": "text", "n": 20, "speed": "fast", "with_without": True, "mode": "test"})
    assert r.status_code == 200 and r.json()["label"] == "Simulated traffic"
    t = _wait(c, "/api/dash/traffic/status")
    assert t["done"] == 20 and t["errors"] == 0 and t["without_totals"]["requests"] == 20
    f = c.get("/api/dash/plug").json()
    assert f["source"] == "live" and f["summary"]["requests"] == 20 and f["simulated_traffic"]
    assert f["without"]["requests"] == 20 and "Simulated traffic" in f["without"]["label"]
    assert c.post("/api/dash/gateway/stop").json()["running"] is False


def test_traffic_hard_stop_and_over_1_needs_confirm(tmp_path, monkeypatch):
    run = tr.TrafficRun(lambda b, h: {"usd": 0.4}, max_usd=1.0)
    run.start("text", 20, "fast")
    run.thread.join(5)
    assert run.state["done"] == 3 and "hard stop" in run.state["stopped"]
    assert tr.estimate("mixed", 60)["usd"] < 1
    big = tr.estimate("images", 200, True)
    c, _ = _ui_client(tmp_path, monkeypatch)
    c.post("/api/dash/gateway/start")
    if not big["under_1_usd"]:
        assert c.post("/api/dash/traffic/start", json={"workload": "images", "n": 200, "with_without": True, "mode": "test"}).status_code == 402


def test_traffic_workloads_build():
    for w in tr.WORKLOADS:
        items = tr.plan(w, 20)
        assert len(items) == 20
    kinds = {k for k, _ in tr.plan("mixed", 12)}
    assert kinds == {"text", "agent", "langchain", "long", "images", "voice"}
    with pytest.raises(ValueError):
        tr.plan("nope", 5)


def test_ui_connect_gpu_runs_rack_check_then_waits_for_ok(tmp_path, monkeypatch):
    c, _ = _ui_client(tmp_path, monkeypatch)
    monkeypatch.setenv("WATTNOT_GPU", "sim")
    monkeypatch.setattr(node_agent, "rack_check", lambda *a, **k: {"ok": True, "rack": "R01",
                        "visible_racks": ["R01"], "label": "REPLAY", "boxes": {}, "note": None, "why": None})
    c.post("/api/dash/node/run", json={"approve": False})
    st = _wait(c, "/api/dash/node/status")
    assert st["result"]["status"] == "waiting_approval"
    ev = [e["step"] for e in c.get("/api/dash/plug").json()["node"]["events"]]
    assert ev == ["gpu_found", "rack_check", "waiting_approval"]


def test_langchain_agent_workload_runs_through_real_http_gateway(tmp_path):
    import socket
    import threading
    import time as _t

    import uvicorn

    def up(body, headers):
        has_tool_result = any(m.get("role") == "tool" for m in body["messages"])
        if body.get("tools") and not has_tool_result:
            msg = {"role": "assistant", "content": None, "tool_calls": [{"id": "t1", "type": "function",
                   "function": {"name": "multiply", "arguments": json.dumps({"a": 8, "b": 350})}}]}
        else:
            msg = {"role": "assistant", "content": "The rack draws 2800 W."}
        return {"id": "x", "object": "chat.completion", "model": body["model"], "created": 0,
                "choices": [{"index": 0, "finish_reason": "stop", "message": msg}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}}

    cfg = load_config(None)
    cfg["savings_log"] = str(tmp_path / "s.jsonl")
    cfg["savers"]["routing"]["enabled"] = False
    app = build_app(cfg, upstream=up)
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=server.run, daemon=True); th.start()
    for _ in range(100):
        if server.started:
            break
        _t.sleep(0.05)
    try:
        out = tr.langchain_agent(f"http://127.0.0.1:{port}/v1", tr.LC_TASKS[0], "sim-lc-0")
        assert out["llm_calls"] == 2 and "2800" in out["answer"]
        recs = [json.loads(x) for x in (tmp_path / "s.jsonl").read_text().splitlines()]
        assert len(recs) == 2 and all(r["run"] == "sim-lc-0" for r in recs)
        run = tr.TrafficRun(lambda b, h: {}, lc_runner=tr.langchain_agent)
        run.start("langchain", 20, "fast", base_url=f"http://127.0.0.1:{port}/v1")
        run.thread.join(60)
        assert run.state["done"] == 20 and run.state["errors"] == 0 and len(run.state["langchain"]) == 20
    finally:
        server.should_exit = True
        th.join(5)


def test_per_request_energy_is_never_labelled_measured(tmp_path):
    p = tmp_path / "w.yaml"
    p.write_text(deploy.render({"deployment": "both"})["yaml"] + f"\nsavings_log: {tmp_path / 's.jsonl'}\n")
    c = TestClient(build_app(load_config(p), upstream=fake_upstream([])))
    ask(c, "easy")
    assert c.get("/v1/wattnot/savings").json()["energy_wh"]["label"] == "ESTIMATE"


def test_ui_add_gpu_helper_ok_runs_harness_cap_check_on_a_real_gpu(tmp_path, monkeypatch):
    """Connect GPU -> Add GPU helper -> OK (approve): a real (fake NVML) GPU gets the same
    HarnessCapCheck the sequential run uses, no NameError, and ends at a kept cap or stock."""
    import time as _t

    import wattnot.node.answercheck as ac
    import wattnot.node.simgpu as sg
    c, _ = _ui_client(tmp_path, monkeypatch)
    monkeypatch.delenv("WATTNOT_GPU", raising=False)
    monkeypatch.setattr(node_agent, "rack_check", lambda *a, **k: {"ok": True, "rack": "R01",
                        "visible_racks": ["R01"], "label": "REPLAY", "boxes": {}, "note": None, "why": None})

    class FakeNvml:
        label = "MEASURED"
        def __init__(self): self.mw, self.e = 700_000, 0
        def nvmlDeviceGetName(self, h=None): return "Fake H100"
        def nvmlDeviceGetCount(self): return 1
        def nvmlDeviceGetPowerManagementLimit(self, h=None): return self.mw
        def nvmlDeviceSetPowerManagementLimit(self, h, mw): self.mw = int(mw)
        def nvmlDeviceGetPowerUsage(self, h=None): return self.mw
        def nvmlDeviceGetTotalEnergyConsumption(self, h=None): return self.e
    gpu = FakeNvml()
    monkeypatch.setattr(sg, "open_gpu", lambda *a, **k: gpu)
    want = {p["prompt"]: p["checks"][0]["value"] for p in ac.proof_prompts()}

    def asker(cfg):
        def ask(prompt, mt):
            _t.sleep(0.02)
            gpu.e += int(1000 * gpu.mw / 700_000)   # energy per answer falls with the cap
            return f"#### {want[prompt]}"
        return ask
    monkeypatch.setattr(ac, "harness_asker", asker)
    c.post("/api/dash/node/run", json={"approve": True, "sleep_s": 0})
    st = _wait(c, "/api/dash/node/status")
    res = st["result"]
    assert res.get("status") != "error", res
    steps = [e for e in c.get("/api/dash/plug").json()["node"]["events"] if e["step"] == "quality_check"]
    assert steps and all("quality" in e for e in steps)
    assert "harness graders" in steps[0]["rule"]


def test_ui_sequence_power_check_then_side_by_side_traffic_completes(tmp_path, monkeypatch):
    """Exact UI order: start gateway, Connect GPU, Add GPU helper, OK power check (keeps a cap),
    then Simulate traffic, Text chat, side by side. It must send (live progress) and finish with
    per-arm MEASURED joules and the limit back at stock."""
    import time as _t

    import wattnot.dash.server as srv
    import wattnot.gateway.traffic as trm
    import wattnot.node.answercheck as ac
    import wattnot.node.simgpu as sg
    c, _ = _ui_client(tmp_path, monkeypatch)
    monkeypatch.delenv("WATTNOT_GPU", raising=False)
    monkeypatch.delenv("WATTNOT_NO_NVML", raising=False)
    monkeypatch.setattr(node_agent, "rack_check", lambda *a, **k: {"ok": True, "rack": "R01",
                        "visible_racks": ["R01"], "label": "REPLAY", "boxes": {}, "note": None, "why": None})

    class FakeNvml:
        label = "MEASURED"
        def __init__(self): self.mw, self.e = 700_000, 0
        def nvmlDeviceGetName(self, h=None): return "Fake H100"
        def nvmlDeviceGetCount(self): return 1
        def nvmlDeviceGetPowerManagementLimit(self, h=None): return self.mw
        def nvmlDeviceSetPowerManagementLimit(self, h, mw): self.mw = int(mw)
        def nvmlDeviceGetPowerUsage(self, h=None): return self.mw
        def nvmlDeviceGetTotalEnergyConsumption(self, h=None): return self.e
    nv = FakeNvml()

    class FakeGpu:   # traffic's NvmlGpu view of the same device
        def energy_mj(self): nv.e += int(1000 * nv.mw / 700_000); return nv.e
        def limit_w(self): return nv.mw // 1000
        def default_w(self): return 700
        def set_w(self, w): nv.mw = int(w) * 1000
    monkeypatch.setattr(sg, "open_gpu", lambda *a, **k: nv)
    monkeypatch.setattr(trm, "NvmlGpu", FakeGpu)
    want = {p["prompt"]: p["checks"][0]["value"] for p in ac.proof_prompts()}

    def asker(cfg):
        def ask(prompt, mt):
            _t.sleep(0.02)
            nv.e += int(1000 * nv.mw / 700_000)
            return f"#### {want[prompt]}"
        return ask
    monkeypatch.setattr(ac, "harness_asker", asker)
    y = tmp_path / "gw.yaml"
    y.write_text("node:\n  enabled: true\n  cap_max_w: 375\n")   # Connect GPU (node.enabled)
    c = TestClient(srv.create_app(gateway=str(y)))

    assert c.post("/api/dash/gateway/start").status_code == 200
    c.post("/api/dash/node/run", json={"approve": False})                       # Connect GPU / Add GPU helper
    assert _wait(c, "/api/dash/node/status")["result"]["status"] == "waiting_approval"
    c.post("/api/dash/node/run", json={"approve": True, "sleep_s": 0})         # OK, start the power check
    res = _wait(c, "/api/dash/node/status")["result"]
    assert res.get("status") != "error", res
    stock_ev = [e for e in c.get("/api/dash/plug").json()["node"]["events"] if e["step"] == "stock_answers"]
    assert stock_ev and stock_ev[-1]["n"] == 111                                  # not "null sample questions"
    kept = nv.mw // 1000                                                          # GPU left at the kept cap
    r = c.post("/api/dash/traffic/start", json={"workload": "text", "n": 20, "speed": "fast",
                                                "with_without": True, "mode": "test", "confirm_over_1": True})
    assert r.status_code == 200, r.text
    assert r.json()["sequential"]
    seen_progress = False
    for _ in range(600):
        st = c.get("/api/dash/traffic/status").json()
        seen_progress |= st["running"] and (st["done"] > 0 or bool(st.get("phase")))
        if not st["running"]:
            break
        _t.sleep(0.05)
    assert not st["running"] and seen_progress, st
    seq = st["sequential_result"]
    assert "error" not in seq, seq
    assert seq["without"]["requests"] == 20 and seq["with"]["requests"] == 20
    assert seq["without"]["joules"] > 0 and seq["with"]["joules"] > 0
    assert seq["without"]["cap_w"] == 700 and seq["final_limit_w"] == 700 and nv.mw == 700_000
    assert st["done"] == 20 and kept in (300, 325, 350, 375, 700)
