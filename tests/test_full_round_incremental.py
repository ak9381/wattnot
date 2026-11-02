"""Full rounds (modal_stage -> modal_full_round): detached launch, app-list wait before teardown,
and results saved to out/ (synced to the volume every 30 s) block by block and item by item."""
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts" / "e2e"), str(ROOT / "scripts")]
import modal_full_inner as I  # noqa: E402
import modal_full_round as R  # noqa: E402


def test_inner_json_saved_after_every_block(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(I, "sub", lambda cmd, out, t: {"rc": 0, "wall_s": 1.0, "out": out.name})
    monkeypatch.setattr(I.EV, "emit", lambda *a, **k: None)
    srv = types.SimpleNamespace(start=lambda **k: {"ok": True}, stop=lambda: None)
    nv = types.SimpleNamespace(energy_j=lambda: 0.0)
    doc = {"steps": {}}
    a = types.SimpleNamespace(only=["think", "langchain"], limit=1, fake=True)
    I.only_blocks(a, tmp_path, srv, nv, doc, lambda: seen.append(sorted(doc["steps"].get("blocks", {}))))
    assert seen[0] == ["think"] and seen[1] == ["langchain", "think"]


def test_full_round_waits_for_detached_app():
    calls = iter([["ap-1"], ["ap-1"], []])
    r = R.wait_app_stopped(1e12, lister=lambda: next(calls), poll_s=0, sleep=lambda s: None)
    assert r is not None
    src = (ROOT / "scripts" / "e2e" / "modal_full_round.py").read_text()
    assert '"modal", "run", "--detach"' in src and src.index("wait_app_stopped(t0") < src.index("teardown()", src.index("wait_app_stopped(t0"))
    assert "setsid nohup" in (ROOT / "scripts" / "e2e" / "launch_detached.sh").read_text()


def test_langchain_items_land_in_out_dir(tmp_path):
    import langchain_pass as LP
    LP.progress_writer(tmp_path / "langchain", "saver_on")({"i": 0, "correct": True})
    assert json.loads((tmp_path / "langchain" / "langchain_items.jsonl").read_text())["arm"] == "saver_on"
