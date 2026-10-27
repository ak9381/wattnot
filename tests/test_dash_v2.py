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


def test_goal_option_offers_named_presets():
    o = _c().get("/api/dash/v2/options").json()
    goal = next(x for x in o["options"] if x["id"] == "goal")
    labels = " ".join(c["label"] for c in goal["choices"]).lower()
    assert "save" in labels and "balanced" in labels and "fast" in labels
    assert all(goal["help"][k] for k in ("what", "why", "example"))


def test_goal_preset_fills_gates_only_when_missing():
    b = configure.to_plan_body({"model": "glimmer", "goal": "save"})
    assert b["p99_slack_pct"] == 15 and b["min_quality_pct"] == 94
    b = configure.to_plan_body({"model": "glimmer", "goal": "save", "p99_slack_pct": 5})
    assert b["p99_slack_pct"] == 5 and b["min_quality_pct"] == 94


def test_configure_shows_essentials_and_collapsed_advanced():
    import pathlib
    js = pathlib.Path(__file__).resolve().parent.parent.joinpath("web/dash/v2.js").read_text()
    assert 'ESSENTIAL_IDS = ["model", "workload", "goal", "gpu", "room_size"]' in js
    assert "Advanced settings" in js
    assert "Safe defaults are set; open this to change limits, gates and budgets" in js
    assert "Defaults in use" in js
    assert "aria-expanded" in js


def test_report_motion_has_scroll_reveal_and_reduced_motion_guard():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent.joinpath("web/dash")
    js = root.joinpath("v2.js").read_text()
    css = root.joinpath("v2.css").read_text()
    assert "IntersectionObserver" in js
    assert "prefers-reduced-motion" in js
    assert "data-split" in js and "scramble" in js.lower()
    assert "prefers-reduced-motion" in css
    assert "tabular-nums" in css


def test_verify_accepts_real_and_rejects_forged():
    v = _c().get("/api/dash/v2/verify").json()
    by = {r["sample"]: {s["name"]: s["ok"] for s in r["steps"]} for r in v["receipts"]}
    assert by["real-measured"]["signature"] is True
    assert by["forged-edited"]["sha256"] is False
