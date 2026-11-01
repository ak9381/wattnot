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
