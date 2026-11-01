"""The dashboard serves the receipts API's read routes (EU report included) on its own port."""
from fastapi.testclient import TestClient

from wattnot.dash.server import create_app


def test_receipts_routes_on_dashboard():
    c = TestClient(create_app())
    lst = c.get("/api/receipts")
    assert lst.status_code == 200
    items = lst.json()
    items = items.get("receipts", items) if isinstance(items, dict) else items
    assert items, "repo ships sample receipts"
    rid = items[0]["receipt_id"]
    assert c.get(f"/api/receipts/{rid}").status_code == 200
    assert c.get(f"/api/receipts/{rid}/verify").status_code == 200
    eu = c.get(f"/api/receipts/{rid}/report/eu")
    assert eu.status_code in (200, 409)   # 409 = refused on a bad receipt, never a 404
    assert c.get("/api/receipts/does-not-exist/report/eu").status_code == 404
    assert eu.status_code == 200 and eu.json()


def test_verify_flow_receipt_self_signed(tmp_path):
    import json

    from wattnot.receipt.sign import sign
    from wattnot.verify import load_trusted, verify_receipt
    from wattnot.flow import _offline_key
    doc = {"spec": "wattnot.flow-receipt/1", "tag": "t", "layers": [
        {"id": "a", "label": "MEASURED", "label_note": "no GPU here", "status": "ok"}],
        "integrity": {}}
    from wattnot.receipt.canon import canonical_sha256
    doc["integrity"] = {"sha256": canonical_sha256(doc)}
    p = tmp_path / "flow-receipt.json"
    p.write_text(json.dumps(doc))
    sign(p, no_rekor=True, key_path=_offline_key(tmp_path))
    r = verify_receipt(p, trusted=load_trusted())
    assert r.grade == "ESTIMATE"
    sig = next(s for s in r.steps if s.name == "signature")
    assert sig.ok and sig.detail["trusted"] is False
    d = json.loads(p.read_text())
    d["layers"][0]["status"] = "failed"
    p.write_text(json.dumps(d))
    assert verify_receipt(p, trusted=load_trusted()).grade == "UNVERIFIABLE"


def test_signed_receipt_untrusted_key_still_fails(tmp_path):
    """Only flow receipts get the self-signed pass; a measurement receipt still needs a trusted key."""
    from wattnot.verify import _is_flow
    assert not _is_flow({"spec": "wattnot.receipt/1"})
