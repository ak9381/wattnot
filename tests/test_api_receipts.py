"""M9: receipt, verify and report endpoints the frontend needs."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from wattnot.api import app as api_app

S = Path(__file__).resolve().parents[1] / "verifier" / "samples"
EXPECTED = json.loads((S / "expected.json").read_text())


def _client(tmp_path) -> TestClient:
    rc = tmp_path / "receipts"
    rc.mkdir()
    for name in ("real-measured", "forged-edited"):
        r = json.loads((S / f"{name}.json").read_text())
        rid = r["receipt_id"] if name.startswith("real") else "rcpt_20261028_100000_f00001"
        (rc / f"{rid}.json").write_text(json.dumps(r))
        shutil.copy(S / f"{name}.sigstore.json", rc / f"{rid}.sigstore.json")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "ablation.json").write_text('{"label": "ESTIMATE", "arms": []}')
    return TestClient(api_app.create_app(root=tmp_path))


def test_list_receipts(tmp_path):
    rows = _client(tmp_path).get("/api/receipts").json()
    assert len(rows) == 2 and all(r["signed"] for r in rows)
    assert {"receipt_id", "grade", "label", "billed_saving_pct"} <= set(rows[0])


def test_verify_stored_receipts(tmp_path):
    c = _client(tmp_path)
    good = json.loads((S / "real-measured.json").read_text())["receipt_id"]
    rep = c.get(f"/api/receipts/{good}/verify").json()
    assert rep["grade"] == "ESTIMATE"
    assert [s["name"] for s in rep["steps"]] == ["schema", "sha256", "signature", "rekor",
                                                 "grade"]
    assert c.get("/api/receipts/rcpt_20261028_100000_f00001/verify").json()["grade"] == \
        "UNVERIFIABLE"
    assert c.get("/api/receipts/nope/verify").status_code == 404


def test_post_verify_matches_golden_vectors(tmp_path):
    c = _client(tmp_path)
    for name, exp in EXPECTED.items():
        body = {"receipt": json.loads((S / f"{name}.json").read_text()),
                "bundle": json.loads((S / f"{name}.sigstore.json").read_text())}
        assert c.post("/api/verify", json=body).json()["grade"] == exp["grade"], name


def test_post_verify_errors_and_stores_nothing(tmp_path):
    c = _client(tmp_path)
    before = sorted(p.name for p in (tmp_path / "receipts").iterdir())
    assert c.post("/api/verify", content=b"{bad").status_code == 400
    assert c.post("/api/verify", json={"x": 1}).status_code == 400
    r = c.post("/api/verify", json={"receipt": json.loads((S / "real-measured.json").read_text())})
    assert r.json()["grade"] == "UNVERIFIABLE"  # no bundle
    assert sorted(p.name for p in (tmp_path / "receipts").iterdir()) == before


def test_eu_report_formats(tmp_path):
    c = _client(tmp_path)
    rid = json.loads((S / "real-measured.json").read_text())["receipt_id"]
    j = c.get(f"/api/receipts/{rid}/report/eu").json()
    assert any(f["field"] == "energy_per_request" for f in j["fields"])
    assert c.get(f"/api/receipts/{rid}/report/eu?format=csv").text.startswith("receipt_id,")
    assert "<table>" in c.get(f"/api/receipts/{rid}/report/eu?format=html").text
    assert c.get(f"/api/receipts/{rid}/report/eu?format=pdf").status_code == 400


def test_ablation(tmp_path):
    assert _client(tmp_path).get("/api/ablation").json()["label"] == "ESTIMATE"
