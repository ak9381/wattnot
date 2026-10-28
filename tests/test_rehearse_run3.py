import json

from wattnot import rehearse


def test_rehearsal_runs_every_block_and_exits_zero(tmp_path):
    out = tmp_path / "reh"
    rc = rehearse.main(["--out", str(out), "--n-escalation", "20"])
    assert rc == 0
    summary = json.loads((out / "rehearse-summary.json").read_text())
    assert summary["all_ok"] is True
    names = [b["block"] for b in summary["blocks"]]
    for expected in ("probe", "cap_sweep", "phase_ab", "cross_check", "escalation", "gate",
                     "net_j_per_correct", "keep_rollback", "receipt", "leaderboard", "pages"):
        assert expected in names, expected
    assert all(b["status"] == "ok" for b in summary["blocks"])


def test_safety_never_touches_real_gpu_or_cloud(tmp_path):
    summary = rehearse.run_rehearsal(tmp_path / "s")
    assert summary["safety"]["touched_real_gpu"] is False
    assert summary["safety"]["touched_cloud"] is False
    assert summary["safety"]["run3_go_set"] is False
    # the signed receipt is SIMULATED on purpose (fake backend), never MEASURED
    rc = next(b for b in summary["blocks"] if b["block"] == "receipt")
    assert rc["grade"] == "SIMULATED" and rc["steps_ok"] is True
    assert rc["spark_trace_id"].startswith("spark-")


def test_refuses_real_flag_and_run3_go(monkeypatch, tmp_path):
    assert rehearse.main(["--out", str(tmp_path / "a"), "--real"]) == 2
    monkeypatch.setenv("RUN3_GO", "1")
    assert rehearse.main(["--out", str(tmp_path / "b")]) == 2


def test_kept_cap_and_rollback(tmp_path):
    summary = rehearse.run_rehearsal(tmp_path / "k")
    assert summary["kept_cap_w"] == 350
    kr = next(b for b in summary["blocks"] if b["block"] == "keep_rollback")
    assert 325 in kr["rolled_back"]


def test_escalation_block_reports_both_rates(tmp_path):
    summary = rehearse.run_rehearsal(tmp_path / "e")
    esc = next(b for b in summary["blocks"] if b["block"] == "escalation")
    assert esc["escalation_rate_pct"] is not None
    assert esc["small_model_pass_pct"] is not None
    assert round(esc["escalation_rate_pct"] + esc["small_model_pass_pct"], 2) == 100.0
