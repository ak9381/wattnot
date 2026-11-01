"""Overview Meta spend KPI reads the live ledger (temp ledger here; no model call)."""
import json

from fastapi.testclient import TestClient

from wattnot.dash.server import create_app


def test_live_spend_reads_ledger(tmp_path, monkeypatch):
    p = tmp_path / "meta_spend.json"
    monkeypatch.setenv("WATTNOT_META_LEDGER", str(p))
    from wattnot.meta.ledger import SpendLedger
    led = SpendLedger(p)
    assert TestClient(create_app()).get("/api/dash/meta/spend").json()["calls"] == 0
    rid, _ = led.reserve({"model": "muse-spark-1.3", "max_tokens": 10, "messages": []}, "t")
    d = TestClient(create_app()).get("/api/dash/meta/spend").json()
    assert d["available"] and d["label"] == "MEASURED" and d["calls"] == 1
    assert d["total_usd"] > 0 and d["cap_usd"] == 120.0
    assert json.dumps(d).find("key") == -1
