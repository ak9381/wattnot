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


def test_report_has_no_gimmick_animation():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent.joinpath("web/dash")
    js = root.joinpath("v2.js").read_text()
    css = root.joinpath("v2.css").read_text()
    for token in ("IntersectionObserver", "requestAnimationFrame", "data-split",
                  "splitWords", "countUp", "scramble", "rev-row", "tick-wrap"):
        assert token not in js, token
    assert "Math.random" not in js and "Date.now" not in js
    for token in ("rev-row", "tick-wrap", "data-split", "prefers-reduced-motion"):
        assert token not in css, token
    assert "tabular-nums" in css  # numbers still line up, without animation
    assert ".map-wires" in css and ".topic-head" in css


def test_report_is_a_mind_map_with_expand_and_wires():
    import pathlib
    js = pathlib.Path(__file__).resolve().parent.parent.joinpath("web/dash/v2.js").read_text()
    assert "mapTopics" in js and "drawWires" in js and "wireMap" in js
    assert "Expand all" in js and "Collapse all" in js
    assert "Who should pick which?" in js  # banner stays under the map
    for topic in ("Power", "Cost", "Water", "Latency", "Quality", "Safety/Approve",
                  "Rejected options", "Proof"):
        assert topic in js, topic
    assert js.count("Without WattNot") >= 8 and js.count("With WattNot") >= 8
    assert "Pick the baseline if" in js and "Pick WattNot if" in js
    for leaf in ("topic-head", "topic-kids", "aria-expanded"):
        assert leaf in js
    assert "resize" in js  # connectors redraw on resize
    assert "cap325" in js  # rejected 325 W names its source file
    assert "93.7%" in js  # headline quality, unchanged


def test_report_fingerprint_is_stable_across_renders():
    import hashlib
    import json
    import pathlib
    js = pathlib.Path(__file__).resolve().parent.parent.joinpath("web/dash/v2.js").read_text()
    assert "stableStringify" in js and "reportFingerprintData" in js
    assert ".sort()" in js  # keys are sorted before hashing
    c = _c()
    first = c.get("/api/dash/v2/run2").json()
    second = c.get("/api/dash/v2/run2").json()
    assert first == second  # the headline data does not move between loads
    canon = json.dumps(first, sort_keys=True, separators=(",", ":"))
    again = json.dumps(second, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canon.encode()).hexdigest() == hashlib.sha256(again.encode()).hexdigest()
    assert "run_id" not in first  # nothing volatile feeds the fingerprint


def test_stepper_model_cards_have_their_own_short_description():
    import pathlib
    js = pathlib.Path(__file__).resolve().parent.parent.joinpath("web/dash/v2.js").read_text()
    assert "MODEL_BLURB" in js
    assert "30B dense model" in js and "8B dense model" in js
    assert "mixture-of-experts" in js and "hosted on Baseten" in js
    # step 2 no longer repeats one shared description for every card
    assert "d: MODEL_BLURB[c.v]" in js


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
    # run page keeps its block; the signed report builds its leaves from the
    # same rows (mind map), so both read the same measured data
    assert js.count("run2BlockHtml") >= 2
    assert "mapTopics" in js and "run2Rows(S.run2" in js
    assert 'data-view' in js and "Compare" in js
    assert "/api/dash/v2/run2" in js
    lib = root.joinpath("v2lib.js").read_text()
    assert "run2Rows" in lib
    css = root.joinpath("v2.css").read_text()
    assert ".picks" in css and ".conn" in css and ".run2block" in css
