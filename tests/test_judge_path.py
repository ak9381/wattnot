from pathlib import Path

from wattnot.dash import judgepath

ROOT = Path(__file__).resolve().parents[1]


def test_bundle_keeps_eras_apart_and_labels():
    d = judgepath.build(ROOT)
    assert d["run2"]["era"] == "RUN2" and d["meta_judge"]["label"] == "PENDING"
    assert d["stack"]["energy_saved_pct"]["label"] == "ESTIMATE"
    assert d["escalation"]["preview"]["label"] == "MOCK"
    assert d["verify"]["chain_ok"] is True
    assert len(d["trace"]["calls"]) == 4


def test_leaderboard_rows_and_receipt():
    from wattnot.dash import leaderboard
    d = leaderboard.build(ROOT)
    measured = [r for r in d["rows"] if r["status"] == "measured"]
    assert measured[0]["setup"].startswith("Default vLLM") and measured[0]["vs_default_pct"] == 0
    best = [r for r in d["rows"] if r["best"]]
    assert len(best) == 1 and best[0]["gate"] == "passed"
    assert any(r["status"] == "pending" and r["label"] == "ESTIMATE" for r in d["rows"])
    assert leaderboard.trace_id(ROOT).startswith("spark-")


def test_run2_receipt_verifies():
    from wattnot.verify import load_trusted, verify_receipt
    p = ROOT / "receipts/run2/rcpt_20261027_run2_cap350.json"
    rep = verify_receipt(p, trusted=load_trusted())
    assert rep.grade == "ESTIMATE" and all(s.ok for s in rep.steps if s.name != "rekor")
    assert '"spark_trace_id"' in p.read_text()
