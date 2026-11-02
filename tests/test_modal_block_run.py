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


def test_langchain_items_written_incrementally_and_scored(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import langchain_pass as LP
    w = LP.progress_writer(tmp_path, "saver_off")
    w({"i": 0, "correct": True, "steps": 2})
    w({"i": 1, "correct": False, "steps": 6})
    LP.progress_writer(tmp_path, "saver_on")({"i": 0, "correct": True, "steps": 1})
    assert LP.partial_scores(tmp_path) == {"saver_off": {"correct": 1, "done": 2},
                                           "saver_on": {"correct": 1, "done": 1}}


def test_fake_langchain_block_leaves_per_item_lines(tmp_path):
    import subprocess
    out = tmp_path / "lc"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "langchain_pass.py"), "--fake", "--n", "3",
                    "--route", "final-check", "--out", str(out)], check=True, cwd=ROOT,
                   env={**__import__("os").environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'src'}"},
                   capture_output=True, timeout=300)
    lines = (out / "langchain_items.jsonl").read_text().splitlines()
    assert len(lines) == 6 and {json.loads(x)["arm"] for x in lines} == {"saver_off", "saver_on"}


def test_cut_run_keeps_partial_scores(tmp_path):
    got = tmp_path / "inc" / "r-1"
    (got / "langchain").mkdir(parents=True)
    (got / "langchain" / "langchain_items.jsonl").write_text(
        json.dumps({"arm": "saver_off", "i": 0, "correct": True}) + "\n")
    dest = tmp_path / "att" / "r-1"
    dest.parent.mkdir()
    assert B.partial_out(got, dest, "r04e", "r-1", {}, 1) == 5
    p = json.loads((dest / "partial.json").read_text())
    assert p["scores_so_far"] == {"saver_off": {"correct": 1, "done": 1}} and p["status"] == "partial"


def test_detach_prefetch_and_cap():
    full = (ROOT / "scripts" / "e2e" / "modal_full_round.py").read_text()
    assert '"modal", "run", "--detach"' in full and "wait_app_stopped(t0 + cap_s + 600)" in full
    inner = (ROOT / "scripts" / "e2e" / "modal_full_inner.py").read_text()
    assert '"--safetensors-load-strategy", "prefetch"' in inner
    assert B.FS.BLOCK_CAP_S == 90 * 60 and B.plan(["langchain"])["worst_usd"] <= B.FS.ROUND_HARD_USD
    sh = (ROOT / "scripts" / "e2e" / "launch_detached.sh").read_text()
    assert "setsid nohup" in sh


def test_ablate_plan_is_labelled_and_capped():
    p = B.plan(["ablate"])
    assert p["label"] == "ESTIMATE" and p["expected_usd"] < p["worst_usd"] <= B.FS.ROUND_HARD_USD
