"""v2 Configure screen, replay run and verifier endpoints."""

import pytest
from fastapi.testclient import TestClient

from wattnot.dash import configure, run2
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


def test_run2_endpoint_reports_measured_llama_350w():
    r = _c().get("/api/dash/v2/run2")
    assert r.status_code == 200
    d = r.json()
    assert d["model"] == "Llama 3.1 8B Instruct" and d["cap_w"] == 350
    assert d["saved_pct"] == pytest.approx(-27.1, abs=0.05)
    assert d["p99_change_pct"] == pytest.approx(7.7, abs=0.05)
    assert d["quality_stock_pct"] == pytest.approx(93.7, abs=0.05)
    assert d["quality_cap_pct"] == d["quality_stock_pct"]
    assert d["label"] == "MEASURED"
    assert "cap350" in d["source"]


def test_run2_loader_falls_back_when_data_file_missing():
    d = run2.load("/nonexistent-root")
    assert d["from_file"] is False
    assert d["saved_pct"] == pytest.approx(-27.1, abs=0.05)
    assert d["p99_change_pct"] == pytest.approx(7.7, abs=0.05)
    assert d["quality_stock_pct"] == d["quality_cap_pct"] == pytest.approx(93.7, abs=0.05)
    assert "cap350" in d["source"]


def test_stepper_answers_map_onto_existing_options():
    c = configure.apply_stepper({}, save="power", traffic="chat", care="gentle", compare="both")
    assert c == {"goal": "save", "save_target": "power", "workload": "coding", "traffic": "chat",
                 "p99_slack_pct": 5, "min_quality_pct": 97, "care": "gentle", "compare": "both"}
    c = configure.apply_stepper({}, save="balanced", traffic="agents", care="bold")
    assert (c["goal"], c["workload"]) == ("balanced", "classes")
    assert (c["p99_slack_pct"], c["min_quality_pct"]) == (15, 94)
    assert configure.STEPPER_CARE["balanced"] == {"p99_slack_pct": 10, "min_quality_pct": 95}
    for bad in ({"save": "nuclear"}, {"traffic": "fax"}, {"care": "reckless"}, {"compare": "maybe"}):
        with pytest.raises(ValueError):
            configure.apply_stepper({}, **bad)
    o = _c().get("/api/dash/v2/options").json()
    goals = {c["v"] for c in next(x for x in o["options"] if x["id"] == "goal")["choices"]}
    workloads = {c["v"] for c in next(x for x in o["options"] if x["id"] == "workload")["choices"]}
    assert set(configure.STEPPER_SAVE.values()) <= goals | {"save", "balanced"}
    assert set(configure.STEPPER_TRAFFIC.values()) <= workloads


def test_configure_is_a_guided_stepper_with_connected_panel():
    import pathlib
    js = pathlib.Path(__file__).resolve().parent.parent.joinpath("web/dash/v2.js").read_text()
    for q in ("What do you want to save on?", "Which AI are you running?",
              "What kind of traffic?", "How careful should we be?", "How do you want to run it?"):
        assert q in js
    assert "A model is the AI program that answers your users' prompts." in js
    assert "Step ${n} of 5" in js or "Step " in js and "of 5" in js
    assert "stepBack" in js and "stepNext" in js and 'role="radiogroup"' in js
    assert 'type="radio"' in js  # cards are radio inputs, not dropdowns
    assert "<svg" in js and "emoji" not in js.lower()
    assert "Already connected" in js and "not set up" in js
    for item in ("GPU telemetry", "E2E Networks H100", "Meta Muse Spark planner",
                 "Baseten", "Hugging Face", "Sigstore"):
        assert item in js
    assert "Advanced settings" in js and "Defaults in use" in js
    assert "Compare both" in js and "Run without (baseline)" in js and '"both"' in js


def test_before_after_block_on_run_page_and_report():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent.joinpath("web/dash")
    js = root.joinpath("v2.js").read_text()
    assert "Without WattNot vs With WattNot" in js
    assert js.count("run2BlockHtml") >= 3  # run page + signed report share one renderer
    assert 'data-view' in js and "Compare" in js
    assert "/api/dash/v2/run2" in js
    lib = root.joinpath("v2lib.js").read_text()
    assert "run2Rows" in lib
    css = root.joinpath("v2.css").read_text()
    assert ".picks" in css and ".conn" in css and ".run2block" in css
