import json
from wattnot.dash import run2


def test_live_hosted_ab_is_attached_to_run_records(tmp_path):
    assert run2.hosted_ab(tmp_path) is None
    d = tmp_path / "reports" / "hosted-ab"
    d.mkdir(parents=True)
    (d / "live-20261027.json").write_text(json.dumps({"mode": "LIVE", "hosts": {"x": {}}}))
    (d / "live-20261028.json").write_text(json.dumps({"mode": "LIVE", "hosts": {"baseten": {"status": "ran"}}}))
    h = run2.hosted_ab(tmp_path)
    assert h["hosts"]["baseten"]["status"] == "ran"
    assert h["source"] == "reports/hosted-ab/live-20261028.json"


def test_repo_record_carries_the_live_report():
    rec = run2.get_run(None, run2.RUN2_ID)
    assert rec["hosted_ab"]["mode"] == "LIVE"
    assert rec["hosted_ab"]["hosts"]["baseten"]["status"] == "ran"
