"""Configure/launch API and the shared eligibility module. No paid calls."""
import json
import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wattnot import eligibility as E
from wattnot.dash import launch as L
from wattnot.dash.server import create_app

ROOT = Path(__file__).resolve().parents[1]


def test_all_110_features_have_a_verdict_per_provider():
    for p in E.PROVIDERS:
        r = E.check({"provider": p})
        assert len(r["features"]) == 110
        assert all(f["reason"] for f in r["features"])
        assert len(r["models"]) == len(E.MODELS)


def test_no_drift_against_runner_tables():
    for p in ("baseten", "modal_a100"):
        assert not E.check({"provider": p})["problems"]


def test_caps_match_approvals():
    assert L.CAPS["modal_smoke_usd"] == 1.5 and L.CAPS["modal_stage_usd"] == 60.0
    assert L.CAPS["modal_round_usd"] == 10.0 and L.CAPS["baseten_round_usd"] == 8.0
    sp = importlib.util.spec_from_file_location("mfr", ROOT / "scripts/e2e/modal_full_round.py")
    assert "STAGE_CAP_USD = 60.0" in (ROOT / "scripts/e2e/modal_full_round.py").read_text()
    assert L._modal_round_worst() <= 10.0


def test_caps_can_only_be_lowered():
    c = L.norm_config({"provider": "baseten", "caps": {"baseten_round_usd": 50}})
    assert c["caps"]["baseten_round_usd"] <= 8.0


def test_smoke_estimate_under_cap():
    e = L.estimate(L.norm_config({"provider": "modal_a100", "smoke": {"limit": 2}}))
    assert e["available"] and e["total_usd"][1] <= 1.5


@pytest.fixture
def client():
    return TestClient(create_app())


def test_live_start_needs_token(client):
    r = client.post("/api/launch/start", json={"config": {"provider": "baseten"}, "mode": "live"})
    assert r.status_code == 403


def test_live_start_needs_checklist(client, monkeypatch):
    monkeypatch.setattr(L, "checklist", lambda cfg, **k: {"ready": False, "items": []})
    cfg = L.norm_config({"provider": "baseten"})
    tok = client.post("/api/launch/estimate", json={"config": cfg}).json()["confirm_token"]
    r = client.post("/api/launch/start", json={"config": cfg, "mode": "live", "confirm_token": tok})
    assert r.status_code == 409


def test_file_route_refuses_secrets(client):
    assert client.get("/api/launch/file", params={"path": "../.config/wattnot/env"}).status_code == 404
    assert client.get("/api/launch/file", params={"path": "src/wattnot/dash/launch.py"}).status_code == 404


def test_approve_refused_when_check_fails():
    items = L.approvals()
    bad = next(a for a in items if any(c["required"] and c["ok"] is not True for c in a["checks"]))
    with pytest.raises(PermissionError):
        L.decide(bad["id"], "approve", "", "test")


def test_normalize_modal_stage_features():
    f = next(ROOT.glob("data/modal-full-1101/**/stage_features.json"), None)
    if f is None:
        pytest.skip("no recorded Modal stage_features.json")
    out = E.normalize_stage_features(json.loads(f.read_text()), "modal_a100")
    assert out["table"] and all({"id", "status", "reason"} <= set(r) for r in out["table"])
