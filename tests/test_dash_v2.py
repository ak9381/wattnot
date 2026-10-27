"""v2 Configure screen, replay run and verifier endpoints."""

from fastapi.testclient import TestClient

from wattnot.dash import configure
from wattnot.dash.server import create_app


def _c():
    return TestClient(create_app())


def test_options_cover_every_group_and_explain_each():
    o = _c().get("/api/dash/v2/options").json()
    ids = {x["id"] for x in o["options"]}
    for k in ("model", "workload", "concurrency", "profile", "planner", "cap_range", "topk_mass",
              "p99_slack_pct", "min_quality_pct", "approval", "room", "room_size", "gpu", "mode"):
        assert k in ids
    for x in o["options"]:
        assert x["choices"] and all(x["help"][k] for k in ("what", "why", "example"))
    assert o["live_locked"] is True


def test_run_is_replay_and_live_is_refused():
    c = _c()
    body = {"model": "glimmer", "p99_slack_pct": 10, "mode": "replay", "name": "t"}
    r = c.post("/api/dash/v2/run", json=body)
    assert r.status_code == 200 and r.json()["label"] == "REPLAY"
    assert r.json()["choice"]["name"] == "t"
    assert c.post("/api/dash/v2/run", json={"model": "glimmer", "mode": "live"}).status_code == 400


def test_plan_body_mapping():
    b = configure.to_plan_body({"model": "qwen3", "levers": "topk", "strict_quality": True,
                                "room": "none"})
    assert b == {"model": "qwen3", "levers": ["topk"], "strict_quality": True, "room": "none"}


def test_verify_accepts_real_and_rejects_forged():
    v = _c().get("/api/dash/v2/verify").json()
    by = {r["sample"]: {s["name"]: s["ok"] for s in r["steps"]} for r in v["receipts"]}
    assert by["real-measured"]["signature"] is True
    assert by["forged-edited"]["sha256"] is False
