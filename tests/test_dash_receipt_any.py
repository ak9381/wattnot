"""The run receipt panel also shows sweep (wattnot.receipt/1) and round receipts, numbers copied as-is."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from wattnot.dash.server import create_app

INTEG = {"sha256": "ab" * 32, "signer": "offline:test", "rekor": None}


def _client(tmp_path, doc):
    ev = tmp_path / "events.jsonl"
    ev.write_text("{}\n")
    (tmp_path / "receipt.json").write_text(json.dumps(doc))
    return TestClient(create_app(events=ev, replay_only=True))


def test_round_receipt_summary(tmp_path):
    doc = {"kind": "modal-own-gpu-e2e", "round": "r05", "model": "m",
           "results": {"energy_saved_pct": {"value": 76.8, "label": "MEASURED"},
                       "accuracy_with": 0.97},
           "labels": {"energy": "MEASURED"}, "integrity": INTEG}
    r = _client(tmp_path, doc).get("/api/dash/run/receipt")
    assert r.status_code == 200
    v = r.json()
    assert v["kind"] == "summary" and v["hash"] == "ab" * 32 and v["signature"]["signed"]
    assert {"name": "Energy saved", "value": 76.8, "unit": "%", "label": "MEASURED"} in v["facts"]


def test_sweep_receipt_summary(tmp_path):
    doc = {"spec": "wattnot.receipt/1", "receipt_id": "rcpt_x", "run_id": "run_x", "grade": "ESTIMATE",
           "subject": {"model": "m"}, "measurement": {"label": "MEASURED"},
           "result": {"saving_pct": {"mean": 78.0, "ci_low": 66.0, "ci_high": 88.0}}, "integrity": INTEG}
    v = _client(tmp_path, doc).get("/api/dash/run/receipt").json()
    assert v["receipt_id"] == "rcpt_x" and [f["value"] for f in v["facts"]] == [78.0, 66.0, 88.0]


def test_unknown_or_unhashed_still_404(tmp_path):
    assert _client(tmp_path, {"kind": "x", "round": "r1"}).get("/api/dash/run/receipt").status_code == 404
    assert _client(tmp_path, {"hello": 1, "integrity": INTEG}).get("/api/dash/run/receipt").status_code == 404
