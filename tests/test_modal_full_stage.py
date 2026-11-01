"""Option-B Modal stage: plumbing runs end to end on a fake server, labels stay honest, guards hold."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(p):
    spec = importlib.util.spec_from_file_location(p.stem, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_inner_fake_runs_every_step(tmp_path):
    out = tmp_path / "r"
    subprocess.run([sys.executable, "scripts/e2e/modal_full_inner.py", "--fake", "--limit", "1", "--out", str(out)],
                   cwd=ROOT, check=True, timeout=600)
    doc = json.loads((out / "inner.json").read_text())
    S = doc["steps"]
    for k in ("without", "batch_kv", "fp8", "with", "limits", "blocks"):
        assert k in S, k
    assert set(S["blocks"]) == {"agentwaste", "langchain", "eye", "spoken", "think"}
    assert "MEASURED" not in (out / "inner.json").read_text()      # fake never reads as MEASURED
    assert S["without_server"]["prefix_caching"] is False and S["with_server"]["prefix_caching"] is True
    assert doc["cap_probe"]["writable"] is False


def test_strip_leak():
    m = _load(ROOT / "scripts/e2e/modal_full_inner.py")
    assert m.strip_leak("<think>a</think> 42") == "42"


def test_gate_keeps_only_cheaper_and_accurate():
    m = _load(ROOT / "scripts/e2e/modal_full_inner.py")
    b = {"accuracy": 0.9, "j_per_correct": {"value": 100}}
    assert m.gate(b, {"accuracy": 0.89, "j_per_correct": {"value": 80}})["keep"]
    assert not m.gate(b, {"accuracy": 0.85, "j_per_correct": {"value": 80}})["keep"]
    assert not m.gate(b, {"accuracy": 0.9, "j_per_correct": {"value": 120}})["keep"]


def test_paid_round_needs_go_and_stage_cap(monkeypatch):
    m = _load(ROOT / "scripts/e2e/modal_full_round.py")
    monkeypatch.delenv("WATTNOT_MODAL_GO", raising=False)
    assert m.main(["--round", "rtest"]) == 3
    fs = m.full_stage
    assert fs.ROUND_CAP_S / 3600 * fs.GPU_USD_H <= 40
    assert fs.SMOKE_CAP_S / 3600 * fs.GPU_USD_H < 1.0


def test_no_metrics_key_anywhere():
    for p in ("scripts/e2e/modal_full_round.py", "scripts/e2e/modal_full_inner.py", "modal/full_stage.py"):
        assert "BASETEN_METRICS_KEY" not in (ROOT / p).read_text().replace("Never reads BASETEN_METRICS_KEY", "")


def test_untested_paths_fail_soft(tmp_path, monkeypatch):
    """FP8, BudgetChat and a GPU block failing are recorded as FAILED; the round still finishes."""
    import json
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    env = {**__import__("os").environ, "WATTNOT_FAKE_FAIL": "fp8,budget,eye"}
    out = tmp_path / "r"
    subprocess.run([sys.executable, "scripts/e2e/modal_full_inner.py", "--fake", "--out", str(out),
                    "--limit", "1"], cwd=root, env=env, check=True, timeout=600)
    d = json.loads((out / "inner.json").read_text())
    assert "with" in d["steps"] and not d.get("fatal")
    assert set(d["failed"]) >= {"fp8", "thinking_budget", "eye_check"}
    assert d["steps"]["fp8"]["kept"] is False
    sys.path[:0] = [str(root / "src")]
    from wattnot.e2e.fullstack import gpu_feature_table
    t = {r["id"]: r["status"] for r in gpu_feature_table(d["failed"])}
    assert t["fp8"] == t["eye_check"] == "FAILED" and t["power_caps"] == "OFF"
