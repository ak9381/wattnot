"""Hosted providers (Modal, Baseten): no root, so no power cap. Side by side still runs, the cap
step is skipped cleanly and labelled, and energy is MEASURED (NVML) or ESTIMATE (tokens)."""
import json

from wattnot.gateway import traffic as tr
from wattnot.gateway.plug import _seq, measured


class NoRootGpu:   # Modal: NVML reads work, setting the limit is refused
    def __init__(self):
        self.e, self.sets = 0, 0

    def energy_mj(self): return self.e
    def limit_w(self): return 400
    def default_w(self): return 400

    def set_w(self, w):
        self.sets += 1
        raise PermissionError("NVML_ERROR_NO_PERMISSION")


def _call(g, j):
    ans = dict(zip(tr.EASY, map(str, tr.EASY_ANS)))
    def call(b, *_):
        if g is not None and not getattr(g, "estimate", False):
            g.e += j * 1000
        return {"text": ans[b["messages"][-1]["content"]], "tokens": 50, "usd": 0.001, "joules": j}
    return call


def test_nvml_gpu_can_cap_probe_false_on_permission_error():
    g = tr.NvmlGpu.__new__(tr.NvmlGpu)
    g.limit_w = lambda: 400
    g.set_w = lambda w: (_ for _ in ()).throw(RuntimeError("no permission"))
    assert g.can_cap() is False


def test_modal_like_gpu_runs_savers_only_measured(tmp_path):
    g = NoRootGpu()
    g.can_cap = lambda: False
    ev = []
    run = tr.TrafficRun(_call(g, 50), direct=_call(g, 80), gpu=g, cap_w=375, answer_check=lambda: 1 / 0,
                        event=lambda **k: ev.append(k), result_path=tmp_path / "sequential.json")
    run.start("text", 20, "fast", True, mode="test")
    run.thread.join(5)
    r = json.loads((tmp_path / "sequential.json").read_text())
    assert "error" not in r and g.sets == 0
    assert r["cap_available"] is False and r["answer_check"]["skipped"]
    assert r["answer_check"]["why_not"] == [tr.CAP_UNAVAILABLE]
    assert r["energy_label"] == "MEASURED" and r["energy_saved_pct"] == 37.5
    assert r["with"]["correct"] == 20 and r["agreement"] == 1.0
    assert any(e.get("step") == "cap_unavailable" for e in ev)
    s = _seq(tmp_path / "savings.jsonl")
    assert s["cap_note"] == tr.CAP_UNAVAILABLE and s["label"].startswith("MEASURED")


def test_baseten_like_estimate_meter(tmp_path):
    g = tr.EstimateMeter()
    run = tr.TrafficRun(_call(g, 5), direct=_call(g, 10), gpu=g, result_path=tmp_path / "sequential.json")
    run.start("text", 20, "fast", True, mode="test")
    run.thread.join(5)
    r = json.loads((tmp_path / "sequential.json").read_text())
    assert "error" not in r and r["cap_available"] is False
    assert r["without"]["joules"] == 200.0 and r["with"]["joules"] == 100.0
    assert r["energy_label"] == "ESTIMATE" and r["with"]["label"].startswith("ESTIMATE")
    assert run.state["phase"] == "done"
    m = measured(tmp_path / "savings.jsonl")
    assert m["label"].startswith("ESTIMATE") and m["cap_note"]
    assert _seq(tmp_path / "savings.jsonl")["label"].startswith("ESTIMATE")


def test_seq_hard_stop_on_spend(tmp_path):
    g = tr.EstimateMeter()
    run = tr.TrafficRun(_call(g, 5), direct=_call(g, 10), gpu=g, result_path=tmp_path / "sequential.json")
    run.seq_max_usd = 0.0045
    run.start("text", 20, "fast", True, mode="test")
    run.thread.join(5)
    r = json.loads((tmp_path / "sequential.json").read_text())
    assert r["without"]["requests"] == 5 and run.state["stopped"].startswith("hard stop")


def test_upstream_headers_from_env(monkeypatch):
    from wattnot.gateway.build import upstream_headers
    monkeypatch.setenv("MK", "k1")
    cfg = {"upstream": {"headers_env": {"Modal-Key": "MK", "Modal-Secret": "MISSING_X"}}}
    assert upstream_headers(cfg) == {"Modal-Key": "k1"}


def test_remote_nvml_gpu_reads_counter_and_never_caps(monkeypatch):
    import io
    import urllib.request
    seen = {}

    def fake(req, timeout=0):
        seen["h"] = dict(req.header_items())
        return io.BytesIO(json.dumps({"energy_mj": 12345}).encode())
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    g = tr.RemoteNvmlGpu("http://x/wattnot/nvml", {"Modal-Key": "k"})
    assert g.energy_mj() == 12345 and g.can_cap() is False and g.limit_w() is None
    assert seen["h"].get("Modal-key") == "k"
