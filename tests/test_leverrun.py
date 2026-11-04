import json
import threading
import time
from types import SimpleNamespace

import pytest

from wattnot.node import leverrun as lr


class FakeDocker:
    def __init__(self, argv=None, apply=True):
        self.cur = list(argv or lr.glimmer_args(lr.MODEL, lr.STOCK))
        self.restarts, self.apply = [], apply

    def argv(self):
        return list(self.cur)

    def restart(self, argv):
        self.restarts.append(list(argv))
        if self.apply or argv == lr.glimmer_args(lr.MODEL, lr.STOCK):
            self.cur = list(argv)


class FakeGpu:
    def __init__(self): self.e = 0
    def energy_mj(self):
        self.e += 1000 if "--quantization" in " ".join(self.docker.cur) else 2000
        return self.e
    def power_w(self): return 300.0


def asker(answer="42", stop_after=None):
    def make(stop=None):
        n = {"i": 0}
        def ask(p):
            n["i"] += 1
            if stop is not None and stop.is_set():
                raise RuntimeError("stopped from the UI")
            return answer
        return ask
    return make


def run(lever, docker, gpu=None, ask=None, **kw):
    gpu = gpu or FakeGpu(); gpu.docker = docker
    r = lr.LeverRun(gpu, docker=docker, healthy=lambda: True, asker=ask or asker(), n_prompts=4,
                    sleep=lambda s: time.sleep(0.001), sample_s=0.001, **kw)
    r.start(lever)
    r.thread.join(5)
    return r, r.status()


def test_stock_argv_matches_running_container():
    want = ["vllm", "serve", lr.MODEL, "--port", "8000", "--served-model-name", lr.MODEL, "--reasoning-parser",
            "muse_glimmer", "--enable-auto-tool-choice", "--tool-call-parser", "muse_glimmer", "--max-model-len",
            "8192", "--gpu-memory-utilization", "0.90", "--max-num-seqs", "64", "--limit-mm-per-prompt.image", "1",
            "--no-enable-prefix-caching"]
    assert lr.glimmer_args(lr.MODEL, lr.STOCK) == want and lr.is_stock(want) == []


@pytest.mark.parametrize("lever,flag", [("fp8", "--quantization fp8"), ("kv_cache", "--kv-cache-dtype fp8"),
                                        ("batching", "--max-num-seqs 512")])
def test_each_lever_changes_one_flag_and_restores_stock(lever, flag):
    d = FakeDocker()
    r, s = run(lever, d)
    assert flag in " ".join(d.restarts[0])
    assert d.cur == lr.glimmer_args(lr.MODEL, lr.STOCK)
    assert s["restore"]["verified"] and not s["running"]
    assert s["arms"]["without"]["joules"] > 0 and s["arms"]["without"]["labels"]["water_l"].startswith("ESTIMATE")


def test_water_and_cost_math():
    t = lr.arm_totals(3.6e6)
    assert t["water_l"] == 0.36 and t["usd"] == 0.1387 and t["wh"] == 1000


def test_fp8_passing_gate_is_kept_and_measured():
    d = FakeDocker()
    _, s = run("fp8", d)
    assert s["result"]["kept"] and s["result"]["label"].startswith("MEASURED")


def test_changed_answers_fail_gate_and_restore():
    d = FakeDocker()
    calls = {"n": 0}
    def mk(stop=None):
        def ask(p):
            calls["n"] += 1
            return "1" if calls["n"] <= 4 else "2"
        return ask
    _, s = run("fp8", d, ask=mk)
    assert not s["result"]["kept"] and "answers changed" in s["result"]["why"]
    assert d.cur == lr.glimmer_args(lr.MODEL, lr.STOCK) and s["restore"]["verified"]


def test_stop_restores_stock():
    d = FakeDocker()
    gate = threading.Event()
    def mk(stop=None):
        def ask(p):
            if "--quantization" in " ".join(d.cur):
                gate.set(); time.sleep(0.05)
            if stop.is_set():
                raise RuntimeError("stopped from the UI")
            return "4"
        return ask
    gpu = FakeGpu(); gpu.docker = d
    r = lr.LeverRun(gpu, docker=d, healthy=lambda: True, asker=mk, n_prompts=4, sleep=lambda s: time.sleep(0.001), sample_s=0.001)
    r.start("fp8"); gate.wait(2); r.stop(); r.thread.join(5)
    s = r.status()
    assert s["stopped"] and "stopped" in s["error"] and d.cur == lr.glimmer_args(lr.MODEL, lr.STOCK)
    assert s["restore"]["verified"]


def test_error_mid_trial_still_restores():
    d = FakeDocker()
    def mk(stop=None):
        def ask(p):
            if "--kv-cache-dtype" in " ".join(d.cur):
                raise OSError("connection reset")
            return "4"
        return ask
    _, s = run("kv_cache", d, ask=mk)
    assert "connection reset" in s["error"] and d.cur == lr.glimmer_args(lr.MODEL, lr.STOCK)


def test_flag_not_applied_restores():
    d = FakeDocker(apply=False)
    _, s = run("batching", d)
    assert not s["result"]["kept"] and "not applied" in s["result"]["why"] and s["restore"]["verified"]


def test_refuses_when_busy_or_not_stock_or_bad_lever():
    gpu = FakeGpu(); gpu.docker = FakeDocker()
    r = lr.LeverRun(gpu, docker=gpu.docker, healthy=lambda: True, busy=lambda: "Another GPU job is running")
    with pytest.raises(RuntimeError, match="Another GPU job"):
        r.start("fp8")
    d = FakeDocker(argv=lr.glimmer_args(lr.MODEL, lr.dataclasses.replace(lr.STOCK, quantization="fp8")))
    r = lr.LeverRun(gpu, docker=d, healthy=lambda: True)
    with pytest.raises(RuntimeError, match="not stock"):
        r.start("fp8")
    with pytest.raises(ValueError):
        r.plan("power")
    r = lr.LeverRun(gpu, docker=FakeDocker(), healthy=lambda: False)
    with pytest.raises(RuntimeError, match="not answering"):
        r.start("fp8")


def test_docker_restart_is_by_name_never_pkill():
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd); return SimpleNamespace(returncode=0, stdout="", stderr="")
    lr.Docker(run=fake).restart(lr.glimmer_args(lr.MODEL, lr.STOCK))
    assert calls[0] == ["docker", "rm", "-f", "glimmer"] and calls[1][:5] == ["docker", "run", "-d", "--name", "glimmer"]
    assert not any("pkill" in " ".join(c) for c in calls)
    src = open(lr.__file__).read()
    assert "pkill" not in src.replace("no pkill", "")


def test_restore_restarts_when_not_stock():
    d = FakeDocker(argv=lr.glimmer_args(lr.MODEL, lr.dataclasses.replace(lr.STOCK, max_num_seqs=512)))
    r = lr.LeverRun(None, docker=d, healthy=lambda: True, sleep=lambda s: None)
    out = r.restore()
    assert out["restarted"] and out["verified"] and len(d.restarts) == 1


def test_dash_lever_routes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from wattnot.dash.server import create_app
    monkeypatch.chdir(tmp_path)
    y = tmp_path / "w.yaml"
    y.write_text(f"savings_log: {tmp_path / 's.jsonl'}\n")
    monkeypatch.setenv("WATTNOT_SAVINGS_LOG", str(tmp_path / "s.jsonl"))
    monkeypatch.setenv("WATTNOT_NODE_LOG", str(tmp_path / "n.jsonl"))
    app = create_app(gateway=y)
    c = TestClient(app)
    p = c.get("/api/dash/lever/plan?lever=fp8").json()
    assert p["change"] == {"quantization": "fp8"} and p["restores_stock"] and p["target"].startswith("http://127.0.0.1:8000")
    assert c.get("/api/dash/lever/plan?lever=nope").status_code == 400
    assert c.post("/api/dash/lever/start", json={"lever": "fp8"}).status_code == 409   # no GPU / vLLM here
    st = c.get("/api/dash/lever/status").json()
    assert st["running"] is False and "server" in st
    sc = c.post("/api/dash/lever/selfcheck", json={}).json()
    assert set(sc["plans"]) == {"fp8", "kv_cache", "batching"} and "restore" not in sc
    app.state.traffic.state["running"] = True
    app.state.lever_run.gpu = object()
    r = c.post("/api/dash/lever/start", json={"lever": "fp8"})
    assert r.status_code == 409 and "Another GPU job" in r.json()["detail"]
    assert "lever.js" in c.get("/plug.html").text


def test_lever_panel_is_in_the_setup_view_the_go_live_drawer_shows():
    from pathlib import Path
    h = (Path(__file__).resolve().parents[1] / "web" / "dash" / "plug.html").read_text()
    setup = h[h.index('<section id="setup"'):h.index('<section id="live"')]
    assert 'id="lever"' in setup and 'id="lvStart"' in setup and h.count('id="lever"') == 1
