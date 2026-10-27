"""The dashboard approval endpoints: list, approve, reject, undo, all JSON."""
from __future__ import annotations

from fastapi.testclient import TestClient

from wattnot.approvals import Approvals, CallbackApplier, Guard
from wattnot.approvals import model as m
from wattnot.approvals.appliers import MockPowerCapApplier
from wattnot.approvals.risk import RiskConfig
from wattnot.dash.server import create_app


def cap_spec(before, after):
    return {"kind": m.POWER_CAP, "title": f"Cap GPU 0 at {after} W",
            "summary": f"Lower the power cap from {before} W to {after} W.",
            "change": {"target": "gpu:0", "gpu": 0,
                       "before": {"power_cap_w": before}, "after": {"power_cap_w": after}},
            "savings": [{"metric": "energy", "value": 9.0, "unit": "%", "label": "MEASURED"}],
            "checks": {"quality": {"passed": True}, "p99": {"passed": True}}}


def filed(tmp_path, before=700, after=400):
    """A pending high-risk proposal in tmp_path, filed outside the dashboard."""
    apps = Approvals(tmp_path, appliers={"power_cap": CallbackApplier(
        lambda p: {"gpu": 0, "before_w": 700, "after_w": 400}, lambda t: None)})
    g = Guard(apps, RiskConfig())
    return g.submit(cap_spec(before, after), gates_passed=True)["proposal"]["id"]


def app_for(tmp_path, **kw):
    app = create_app(approvals_dir=tmp_path, **kw)
    return app, TestClient(app)


def test_list_buckets_and_item_shape(tmp_path):
    pid = filed(tmp_path)
    _, c = app_for(tmp_path)
    body = c.get("/api/dash/approvals").json()
    assert set(body) == {"pending", "applied", "recent", "config",
                         "auto_approve", "mode", "now"}
    assert [p["id"] for p in body["pending"]] == [pid]
    assert body["applied"] == [] and body["recent"] == []
    assert body["mode"] == "mock" and body["auto_approve"] is False
    item = body["pending"][0]
    assert item["risk"]["level"] == "high" and item["risk"]["reasons"]
    assert item["change"] == {"before": {"power_cap_w": 700},
                              "after": {"power_cap_w": 400}}
    assert item["seconds_left"] > 0 and item["decision"] is None
    assert item["undo_available"] is False and item["content_sha256"]


def test_approve_then_apply_then_undo_restores_the_mock_cap(tmp_path):
    pid = filed(tmp_path)
    app, c = app_for(tmp_path)
    r = c.post("/api/dash/approve", json={"id": pid}).json()
    assert r["status"] == "approved"
    assert r["decision"]["by"] == "dashboard:operator"
    assert r["decision"]["via"] == "http"
    # approving twice is a conflict
    r = c.post("/api/dash/approve", json={"id": pid})
    assert r.status_code == 409 and r.json()["code"] == "conflict"
    # approve without apply leaves it unapplied: undo is refused
    r = c.post("/api/dash/undo/" + pid, json={})
    assert r.status_code == 409
    pid2 = filed(tmp_path)
    r = c.post("/api/dash/approve", json={"id": pid2, "apply": True}).json()
    assert r["status"] == "applied" and r["undo_available"] is True
    mock = app.state.guard.approvals.appliers["power_cap"]
    assert mock.caps[0] == 400
    r = c.post("/api/dash/undo/" + pid2, json={"by": "op"}).json()
    assert r["status"] == "rolled_back"
    assert mock.caps[0] == 700
    body = c.get("/api/dash/approvals").json()
    assert [p["id"] for p in body["applied"]] == []
    assert pid2 in [p["id"] for p in body["recent"]]


def test_reject(tmp_path):
    pid = filed(tmp_path)
    _, c = app_for(tmp_path)
    r = c.post("/api/dash/reject", json={"id": pid, "by": "op", "note": "no"}).json()
    assert r["status"] == "rejected" and r["decision"]["note"] == "no"
    body = c.get("/api/dash/approvals").json()
    assert body["pending"] == [] and [p["id"] for p in body["recent"]] == [pid]


def test_expired_cannot_be_approved(tmp_path):
    apps = Approvals(tmp_path)
    p = apps.propose(cap_spec(700, 400))
    p["expires_at"] = m.iso(apps.clock() - 1)
    p["content_sha256"] = m.content_hash(p)
    apps.store.put(p)
    _, c = app_for(tmp_path)
    r = c.post("/api/dash/approve", json={"id": p["id"]})
    assert r.status_code == 409
    body = c.get("/api/dash/approvals").json()
    assert body["pending"] == []
    assert body["recent"][0]["status"] == "expired"


def test_wrong_content_type_is_rejected(tmp_path):
    pid = filed(tmp_path)
    _, c = app_for(tmp_path)
    for url in ("/api/dash/approve", "/api/dash/reject"):
        r = c.post(url, content=f'{{"id": "{pid}"}}',
                   headers={"content-type": "text/plain"})
        assert r.status_code == 415 and r.json()["code"] == "unsupported_media_type"
    r = c.post("/api/dash/undo/" + pid, content="{}",
               headers={"content-type": "text/plain"})
    assert r.status_code == 415
    # nothing was decided by the refused calls
    assert c.get("/api/dash/approvals").json()["pending"][0]["status"] == "pending"


def test_unknown_ids_are_404(tmp_path):
    _, c = app_for(tmp_path)
    assert c.post("/api/dash/approve", json={"id": "prop_nope"}).status_code == 404
    assert c.post("/api/dash/reject", json={"id": "prop_nope"}).status_code == 404
    assert c.post("/api/dash/undo/prop_nope", json={}).status_code == 404


def test_hosted_replay_uses_the_mock_even_with_live_apply(tmp_path):
    app = create_app(approvals_dir=tmp_path, replay_only=True, live_apply=True)
    c = TestClient(app)
    assert c.get("/api/dash/approvals").json()["mode"] == "mock"
    assert isinstance(app.state.guard.approvals.appliers["power_cap"],
                      MockPowerCapApplier)


def test_token_gate_covers_the_approval_endpoints(tmp_path):
    _, c = app_for(tmp_path, token="s3cret")
    assert c.get("/api/dash/approvals").status_code == 401
    assert c.post("/api/dash/approve", json={"id": "x"}).status_code == 401


def test_auto_approve_flag_is_surfaced(tmp_path):
    _, c = app_for(tmp_path, auto_approve=True)
    body = c.get("/api/dash/approvals").json()
    assert body["auto_approve"] is True
    assert body["config"]["approval_timeout_s"] == 600


def test_orchestrate_owned_change_is_not_applied_by_the_dashboard(tmp_path):
    from wattnot.approvals import risk as rk
    from wattnot.approvals.guard import Guard
    app = create_app(approvals_dir=tmp_path, replay_only=True)
    g = Guard(app.state.approvals, rk.RiskConfig())
    spec = {"kind": "power_cap", "title": "t", "summary": "s", "label": "MOCK",
            "source": {"applier": "orchestrate"},
            "change": {"gpu": 0, "gpus": [0], "before": {"power_cap_w": 700},
                       "after": {"power_cap_w": 300}}}
    pid = g.submit(spec, gates_passed=True, stock_cap_w=700, tdp_w=700)["proposal"]["id"]
    c = TestClient(app)
    r = c.post("/api/dash/approve", json={"id": pid, "apply": True}).json()
    assert r["applies_on_approve"] is False
    assert r["status"] == "approved" and r["applied"] is None
