import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts" / "e2e")]
import modal_block_run as B  # noqa: E402


def test_plan_is_under_round_cap_and_labelled():
    p = B.plan(["langchain"])
    assert p["label"] == "ESTIMATE"
    assert p["worst_usd"] <= B.FS.ROUND_HARD_USD
    assert p["expected_usd"] < p["worst_usd"]


def test_paid_run_refuses_without_go(monkeypatch, capsys):
    monkeypatch.delenv(B.FS.GO_ENV, raising=False)
    monkeypatch.setattr(B, "DATA", ROOT / "data" / "modal-full-1101")
    rc = B.main(["--attach", "r04e", "--only", "langchain", "--run-id", "t-nogo-x"])
    assert rc in (2, 3)   # no round.json in a bare checkout, or no GO


def test_fake_run_attaches_signed_without_touching_round(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "DATA", tmp_path)
    rdir = tmp_path / "r04e"
    rdir.mkdir()
    (rdir / "round.json").write_text('{"round": "r04e"}')
    before = sorted(p.name for p in rdir.iterdir())
    assert B.main(["--attach", "r04e", "--only", "langchain", "--fake", "--run-id", "r04e-lc-t"]) == 0
    att = tmp_path / "fake" / "attachments" / "r04e" / "r04e-lc-t"
    doc = json.loads((att / "attachment.json").read_text())
    assert doc["run_id"] == "r04e-lc-t" and doc["attaches_to"] == "r04e" and doc["label"] == "MOCK"
    assert (att / "attachment.sigstore.json").exists()
    assert sorted(p.name for p in rdir.iterdir()) == before
    assert B.main(["--attach", "r04e", "--only", "langchain", "--fake", "--run-id", "r04e-lc-t"]) == 2


def test_round_view_lists_attachments(tmp_path, monkeypatch):
    from wattnot.dash import launch as L
    monkeypatch.setattr(L, "ROOT", tmp_path)
    d = tmp_path / "attachments" / "r04e" / "r04e-lc-1"
    d.mkdir(parents=True)
    (d / "attachment.json").write_text(json.dumps({"run_id": "r04e-lc-1", "block": "langchain",
                                                   "status": "passed", "integrity": {"sha256": "ab"}}))
    got = L.round_attachments(tmp_path / "r04e")
    assert got[0]["run_id"] == "r04e-lc-1" and got[0]["signed"] is False


def test_block_run_is_detached_and_waits_for_the_app():
    src = (ROOT / "scripts" / "e2e" / "modal_block_run.py").read_text()
    assert '"modal", "run", "--detach"' in src
    seq = iter([["ap-1"], None, ["ap-1"], []])
    clock = iter(range(0, 10000, 60))
    sleeps = []
    got = B.wait_app_stopped(5000, lister=lambda: next(seq), sleep=sleeps.append, now=lambda: next(clock))
    assert got == {"stopped": True, "polls": 4} and len(sleeps) == 3   # None (list failed) never counts as stopped
    never = B.wait_app_stopped(120, lister=lambda: ["ap-1"], sleep=lambda s: None,
                               now=iter(range(0, 1000, 60)).__next__)
    assert never["stopped"] is False and never["why"] == "deadline"
