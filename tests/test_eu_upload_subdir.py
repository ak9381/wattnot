"""EU report: bad signature with a good hash is a 409 (not a 500), uploads, and receipt subfolders."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from wattnot.api import app as api_app

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "receipts" / "rerun-1031"
RID = "rcpt_20261031_rerun_cap325"


def _bad_bundle() -> dict:
    b = json.loads((SRC / f"{RID}.sigstore.json").read_text())
    sig = b["signature"]
    b["signature"] = ("0" if sig[0] != "0" else "1") + sig[1:]   # hash untouched, signature wrong
    return b


def _root(tmp_path, bad: bool = False) -> Path:
    d = tmp_path / "receipts" / "rerun-1031"
    d.mkdir(parents=True)
    shutil.copy(SRC / f"{RID}.json", d / f"{RID}.json")
    if bad:
        (d / f"{RID}.sigstore.json").write_text(json.dumps(_bad_bundle()))
    else:
        shutil.copy(SRC / f"{RID}.sigstore.json", d / f"{RID}.sigstore.json")
    return tmp_path


def test_api_reads_receipts_in_subfolders(tmp_path):
    c = TestClient(api_app.create_app(root=_root(tmp_path)))
    ids = [r["receipt_id"] for r in c.get("/api/receipts").json()]
    assert RID in ids
    j = c.get(f"/api/receipts/{RID}/report/eu").json()
    assert j["signed"] is True and j["fields"]
    assert c.get(f"/api/receipts/{RID}/verify").json()["path"] == f"receipts/rerun-1031/{RID}.json"
    assert c.get("/api/receipts/..%2Fsecret/report/eu").status_code == 404


def test_api_bad_signature_good_hash_is_409(tmp_path):
    c = TestClient(api_app.create_app(root=_root(tmp_path, bad=True)), raise_server_exceptions=False)
    r = c.get(f"/api/receipts/{RID}/report/eu")
    assert r.status_code == 409 and r.json()["code"] == "bad_signature"


def test_cli_bad_signature_good_hash_refuses(tmp_path):
    from wattnot.cli import main
    root = _root(tmp_path, bad=True)
    with pytest.raises(SystemExit) as e:   # parser.error, not a NameError traceback
        main(["report", "eu", "--receipts", str(root / "receipts" / "rerun-1031" / f"{RID}.json"),
              "--out", str(tmp_path / "eu")])
    assert e.value.code == 2
    assert not (tmp_path / "eu.csv").exists()


def test_upload_signed_unsigned_tampered(tmp_path):
    c = TestClient(api_app.create_app(root=tmp_path), raise_server_exceptions=False)
    rec = json.loads((SRC / f"{RID}.json").read_text())
    good = json.loads((SRC / f"{RID}.sigstore.json").read_text())
    j = c.post("/api/report/eu/upload", json={"receipt": rec, "bundle": good}).json()
    assert j["signed"] is True and j["receipt_id"] == RID
    assert c.post("/api/report/eu/upload?format=csv", json={"receipt": rec, "bundle": good}).text.startswith("receipt_id,")
    assert "<table>" in c.post("/api/report/eu/upload?format=html", json={"receipt": rec, "bundle": good}).text
    u = c.post("/api/report/eu/upload", json={"receipt": rec}).json()
    assert u["signed"] is False and all(f["label"] != "MEASURED" for f in u["fields"])
    assert c.post("/api/report/eu/upload", json={"receipt": rec, "bundle": _bad_bundle()}).status_code == 409
    t = json.loads(json.dumps(rec)); t["receipt_id"] = "rcpt_tampered"
    r = c.post("/api/report/eu/upload", json={"receipt": t, "bundle": good})
    assert r.status_code == 409 and r.json()["code"] == "hash_mismatch"
    assert c.post("/api/report/eu/upload", content=b"nope").status_code == 400
    assert c.post("/api/report/eu/upload", json={"x": 1}).status_code == 400
    assert not (tmp_path / "receipts").exists()   # nothing stored


def test_dashboard_server_has_the_upload_route(tmp_path):
    from wattnot.dash import server
    c = TestClient(server.create_app())
    rec = json.loads((SRC / f"{RID}.json").read_text())
    good = json.loads((SRC / f"{RID}.sigstore.json").read_text())
    r = c.post("/api/report/eu/upload", json={"receipt": rec, "bundle": good})
    assert r.status_code == 200 and r.json()["signed"] is True
    assert c.post("/api/report/eu/upload", json={"receipt": rec, "bundle": _bad_bundle()}).status_code == 409
