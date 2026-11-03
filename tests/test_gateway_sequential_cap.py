"""One GPU, side by side: without at stock, then WattNot at the harness cap after the answer
check; MEASURED NVML joules per arm; the limit always goes back to stock."""
import json

from wattnot.gateway import traffic as tr
from wattnot.gateway.plug import _seq


class FakeGpu:
    def __init__(self):
        self.lim, self.e, self.sets = 700, 0, []

    def energy_mj(self):
        return self.e

    def limit_w(self):
        return self.lim

    def default_w(self):
        return 700

    def set_w(self, w):
        self.sets.append(w); self.lim = w


class Check:
    def __init__(self, ok): self.ok = ok
    def stock(self): return {}
    def at(self, cap): return {"accepted": self.ok, "why_not": [] if self.ok else ["1 of 5 answers changed"]}


def _run(tmp_path, ok=True, boom=False):
    g = FakeGpu()
    ans = dict(zip(tr.EASY, map(str, tr.EASY_ANS)))
    def call(b, *_):
        if boom and g.lim == 375:
            raise RuntimeError("x")
        g.e += 1000 * (g.lim // 10)                   # J per request scale with the power limit
        return {"text": ans[b["messages"][-1]["content"]], "tokens": 50, "usd": 0}
    ev = []
    run = tr.TrafficRun(call, direct=call, gpu=g, cap_w=375, answer_check=lambda: Check(ok),
                        event=lambda **k: ev.append(k), result_path=tmp_path / "sequential.json")
    run.start("text", 20, "fast", True)
    run.thread.join(5)
    return g, json.loads((tmp_path / "sequential.json").read_text()), ev


def test_sequential_arms_cap_only_on_wattnot_and_restored(tmp_path):
    g, r, ev = _run(tmp_path)
    assert r["without"]["cap_w"] == 700 and r["with"]["cap_w"] == 375
    assert r["without"]["joules"] == 1400.0 and r["with"]["joules"] == 740.0
    assert r["energy_label"] == "MEASURED" and r["energy_saved_pct"] > 0
    assert r["with"]["correct"] == r["without"]["correct"] == 20 and r["no_worse"] and r["agreement"] == 1.0
    assert r["final_limit_w"] == 700 and g.sets[-1] == 700
    assert [e["step"] for e in ev][:2] == ["arm_start", "arm_end"]
    s = _seq(tmp_path / "savings.jsonl")
    assert s["with"]["joules"] == 740.0 and s["label"].startswith("MEASURED")


def test_answer_check_fail_keeps_stock(tmp_path):
    _, r, _ = _run(tmp_path, ok=False)
    assert r["with"]["cap_w"] == 700 and r["final_limit_w"] == 700


def test_error_restores_stock(tmp_path):
    g, r, _ = _run(tmp_path, boom=True)
    assert r["final_limit_w"] == 700 and g.lim == 700


def _agent_run(tmp_path, cap_costs_more=True):
    g = FakeGpu()
    def call(b, *_):
        if tr.req_class(b) == "agent":
            g.e += 1000 * (550 if (g.lim == 375 and cap_costs_more) else 500 if g.lim == 700 else 400)
        else:
            g.e += 1000 * (g.lim // 10)
        return {"text": "x", "tokens": 50, "usd": 0}
    ev = []
    run = tr.TrafficRun(call, direct=call, gpu=g, cap_w=375, answer_check=lambda: Check(True),
                        event=lambda **k: ev.append(k), result_path=tmp_path / "sequential.json")
    run.start("agent", 20, "fast", True)
    run.thread.join(5)
    return g, json.loads((tmp_path / "sequential.json").read_text()), ev, run


def test_cap_rolled_back_for_agent_when_measured_energy_not_down(tmp_path):
    g, r, ev, run = _agent_run(tmp_path)
    d = r["cap_decision"]["agent"]
    assert d["keep_cap"] is False and d["pairs"] == tr.CAP_PROBE_N
    assert d["message"] == "cap rolled back for agent traffic: no energy saving (MEASURED)"
    assert any(e["step"] == "rolled_back" and e.get("workload_class") == "agent" for e in ev)
    # probe at the cap (4 x 550 J), rest back at stock (16 x 500 J): no worse than probe cost
    assert r["with"]["joules"] == 4 * 550 + 16 * 500
    assert run.state["cap_rollback"].startswith("cap rolled back for agent")
    assert r["final_limit_w"] == 700


def test_cap_kept_for_agent_when_measured_energy_down(tmp_path):
    g, r, ev, _ = _agent_run(tmp_path, cap_costs_more=False)
    assert r["cap_decision"]["agent"]["keep_cap"] is True
    assert r["with"]["joules"] == 20 * 400


def test_req_class_from_shape():
    assert tr.req_class({"messages": [{"role": "user", "content": "hi"}]}) == "text"
    assert tr.req_class({"tools": [{}], "messages": []}) == "agent"
