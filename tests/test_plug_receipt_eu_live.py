"""Live gateway traffic from the dashboard: every request has a receipt the live graph can open,
and the EU report is built from the gateway savings log plus the GPU helper's NVML log, labelled."""
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
import wattnot.dash.server as ds  # noqa: E402
from test_gateway_glimmer_e2e import glimmer_like  # noqa: E402

WEB = Path(__file__).parents[1] / "web" / "dash"


def test_receipt_and_eu_report_from_live_gateway_traffic(tmp_path, monkeypatch):
    monkeypatch.setenv("WATTNOT_PLUG_YAML", str(tmp_path / "w.yaml"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ds, "GATEWAY_UPSTREAM", lambda cfg: glimmer_like([]))
    c = TestClient(ds.create_app())
    assert c.post("/api/dash/gateway/start").status_code == 200
    for i in range(3):
        r = c.post("/gw/v1/chat/completions", json={"model": "m", "messages": [{"role": "user", "content": f"q{i}"}]})
        assert r.status_code == 200
    rid = r.headers["x-wattnot-receipt"]
    rc = c.get(f"/api/dash/plug/receipt/{rid}").json()
    assert rc["receipt_id"] == rid and rc["labels"]["tokens"].startswith("MEASURED") and "ESTIMATE" in rc["labels"]["joules"]
    node = Path("out/gateway/node.jsonl"); node.parent.mkdir(parents=True, exist_ok=True)
    node.write_text("\n".join(json.dumps({"kind": "sample", "ts": 1 + i, "power_w": 300.0, "energy_j": 300.0 * i,
                                          "gpu_label": "MEASURED"}) for i in range(5)) + "\n")
    eu = c.get("/api/dash/plug/eu").json()
    assert eu["source"] == "live" and eu["requests"] == 3 and eu["last_receipt"] == rid
    assert eu["energy_saved_kwh"]["label"].startswith("ESTIMATE") and eu["energy_kwh"]["label"]


def test_live_graph_has_receipt_and_eu_views():
    g = (WEB / "graphbar.js").read_text()
    assert "__wnShowReceipt" in g and "__wnShowEU" in g and 'id="gwlEu"' in g and "/api/dash/plug/eu" in g
    p = (WEB / "plug.js").read_text()
    assert "parent.__wnShowReceipt" in p and "parent.__wnShowEU" in p
    assert 'location.replace("graph.html"' in (WEB / "plug.html").read_text()


def test_receipt_verify_and_quality_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("WATTNOT_PLUG_YAML", str(tmp_path / "w.yaml"))
    monkeypatch.setenv("WATTNOT_SIGN_KEY", str(tmp_path / "nokey.pem"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ds, "GATEWAY_UPSTREAM", lambda cfg: glimmer_like([]))
    c = TestClient(ds.create_app())
    c.post("/api/dash/gateway/start")
    ids = [c.post("/gw/v1/chat/completions", json={"model": "m", "messages": [{"role": "user", "content": f"q{i}"}]}).headers["x-wattnot-receipt"] for i in range(2)]
    v = c.get(f"/api/dash/plug/receipt/{ids[1]}/verify").json()
    assert v["id_matches"] and v["chain_ok"] and v["signer"] == "UNSIGNED"
    from wattnot.receipt.sign import generate_test_key
    monkeypatch.setenv("WATTNOT_SIGN_KEY", str(generate_test_key(tmp_path / "k.pem")))
    assert c.get(f"/api/dash/plug/receipt/{ids[0]}/verify").json()["signer"].startswith("offline:")
    q = c.get("/api/dash/plug/quality").json()
    assert q["requests"] == 2 and "ESTIMATE" in q["spend_label"] and "gate_off" in q and len(q["spend_log"]) == 2
