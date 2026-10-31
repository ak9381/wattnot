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
    c = configure.apply_stepper({}, save="power", traffic="chat", care="gentle")
    assert c == {"goal": "save", "save_target": "power", "workload": "coding", "traffic": "chat",
                 "p99_slack_pct": 5, "min_quality_pct": 97, "care": "gentle"}
    c = configure.apply_stepper({}, save="balanced", traffic="agents", care="bold")
    assert (c["goal"], c["workload"]) == ("balanced", "classes")
    assert (c["p99_slack_pct"], c["min_quality_pct"]) == (15, 94)
    assert configure.STEPPER_CARE["balanced"] == {"p99_slack_pct": 10, "min_quality_pct": 95}
    for bad in ({"save": "nuclear"}, {"traffic": "fax"}, {"care": "reckless"}):
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
              "What kind of traffic?", "Your stack", "How careful should we be?", "How do you want to run it?"):
        assert q in js
    assert "A model is the AI program that answers your users' prompts." in js
    assert "Step ${n} of 6" in js or "Step ${n} of 5" in js or "of 6" in js or "of 5" in js
    assert "stepBack" in js and "stepNext" in js and 'role="radiogroup"' in js
    assert 'type="radio"' in js  # cards are radio inputs, not dropdowns
    assert "<svg" in js and "emoji" not in js.lower()
    assert "Already connected" in js and "not set up" in js
    for item in ("GPU telemetry", "E2E Networks H100", "Meta Muse Spark planner",
                 "Baseten", "Hugging Face", "Sigstore"):
        assert item in js
    assert "Advanced settings" in js and "Defaults in use" in js
    assert "This run measures both: baseline first, then WattNot." in js
    assert "COMPARE_CARDS" not in js and 'input[name="compare"]' not in js
    assert "Compare both" not in js and "Run without (baseline)" not in js


def test_before_after_block_on_run_page_and_report():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent.joinpath("web/dash")
    js = root.joinpath("v2.js").read_text()
    assert "Without WattNot vs With WattNot" in js
    # run page keeps its block; the signed report builds its leaves from the
    # same rows (mind map), so both read the same measured data
    assert js.count("run2BlockHtml") >= 2
    assert "mapTopics" in js and "run2Rows(S.selHeadline || S.run2" in js
    assert 'data-view' in js and "Compare" in js
    assert "/api/dash/v2/run2" in js
    lib = root.joinpath("v2lib.js").read_text()
    assert "run2Rows" in lib
    css = root.joinpath("v2.css").read_text()
    assert ".picks" in css and ".conn" in css and ".run2block" in css


def test_old_compare_field_is_ignored():
    assert configure.apply_stepper({}, save="power", compare="both") == \
        {"goal": "save", "save_target": "power"}
    assert configure.apply_stepper({}, compare="maybe") == {}
    assert "compare" not in configure.to_plan_body({"model": "glimmer", "compare": "both"})
    assert not hasattr(configure, "STEPPER_COMPARE")
    c = _c()
    r = c.post("/api/dash/v2/run", json={"model": "glimmer", "mode": "replay", "compare": "both"})
    assert r.status_code == 200 and "compare" not in r.json()["choice"]


def test_run_record_holds_both_phases():
    import pathlib
    rec = run2.build_record(pathlib.Path(__file__).resolve().parent.parent)
    assert rec["id"] and rec["run_name"] and rec["model"] == "Llama 3.1 8B Instruct"
    assert rec["label"] == "REPLAY"
    base, tuned = rec["phases"]["baseline"], rec["phases"]["wattnot"]
    assert base["title"] == "Without WattNot" and tuned["title"] == "With WattNot"
    assert base["label"] == tuned["label"] == "MEASURED"
    assert base["cap_w"] == 700 and tuned["cap_w"] == 350
    assert base["metrics"]["j_per_req"] == pytest.approx(15.959, abs=0.01)
    assert tuned["metrics"]["j_per_req"] == pytest.approx(11.635, abs=0.01)
    assert tuned["metrics"]["saved_pct"] == pytest.approx(-27.1, abs=0.05)
    assert tuned["metrics"]["p99_change_pct"] == pytest.approx(7.7, abs=0.05)
    assert base["metrics"]["quality_pct"] == tuned["metrics"]["quality_pct"] == pytest.approx(93.7, abs=0.05)
    assert len(base["events"]) == len(tuned["events"]) == 3
    assert all(e["label"] == "MEASURED" and e["raw_sha256"] for e in base["events"] + tuned["events"])


def test_runs_endpoints_list_by_name_and_return_records():
    c = _c()
    got = c.get("/api/dash/v2/runs").json()["runs"]
    assert got and all(set(r) >= {"id", "name", "model", "date"} for r in got)
    first = c.get("/api/dash/v2/runs/run2-llama-350w").json()
    assert first["run_name"] == got[0]["name"] == run2.RUN2_NAME
    assert set(first["phases"]) == {"baseline", "wattnot"}
    assert c.get("/api/dash/v2/runs/no-such-run").status_code == 404


def test_runs_discovered_from_disk_so_a_new_run_appears(tmp_path):
    import json

    cloud = tmp_path / "data" / "cloud"
    cloud.mkdir(parents=True)
    # A run saved on Oct 31 (newer than the bundled Oct 27 fixtures) and a non-run JSON that
    # must be ignored because it does not declare the fixture format.
    (cloud / "gap-1031-run.json").write_text(json.dumps({
        "format": run2.FIXTURE_FORMAT, "model": "meta-models/muse-spark-open-1026",
        "recorded_at": "2026-10-31T01:53:41Z",
        "replies": {"q1": {"latency_s": 1.2, "status": 200}}}))
    (cloud / "demo-meter.json").write_text(json.dumps({"watts": 42}))
    (cloud / "recorded-old.json").write_text(json.dumps({
        "format": run2.FIXTURE_FORMAT, "model": "m", "recorded_at": "2026-10-27T00:00:00Z",
        "replies": {}}))

    ids = [r["id"] for r in run2.list_runs(tmp_path)]
    assert ids[0] == run2.RUN2_ID                      # RUN2 stays first
    assert "cloud-gap-1031-run" in ids                 # the Oct 31 run is now selectable
    assert "cloud-demo-meter" not in ids               # the non-run JSON is ignored
    # Newest cloud run is listed ahead of the older one.
    assert ids.index("cloud-gap-1031-run") < ids.index("cloud-recorded-old")

    rec = run2.get_run(tmp_path, "cloud-gap-1031-run")
    assert rec is not None and rec["date"] == "2026-10-31"


def test_replay_plays_baseline_then_wattnot():
    import json
    import pathlib
    bundle = json.loads(pathlib.Path(__file__).resolve().parent.parent.joinpath("data/dash/story.json").read_text())
    from wattnot.dash import runplan
    for model in ("glimmer", "qwen3", "spark_open"):
        p = runplan.plan(bundle, {"model": model})
        brk = p["phase_break"]
        assert 0 < brk < len(p["events"])
        assert [f["phase"] for f in p["events"]] == ["baseline"] * brk + ["wattnot"] * (len(p["events"]) - brk)
        assert p["phases"] == {"baseline": {"title": "Without WattNot", "n_events": brk},
                               "wattnot": {"title": "With WattNot", "n_events": len(p["events"]) - brk}}
    c = _c()
    r = c.post("/api/dash/v2/run", json={"model": "glimmer", "mode": "replay"}).json()
    assert r["phase_break"] == r["phases"]["baseline"]["n_events"]


def test_run_page_shows_phase_indicator():
    import pathlib
    js = pathlib.Path(__file__).resolve().parent.parent.joinpath("web/dash/v2.js").read_text()
    assert "phaseInd" in js and "phase_break" in js
    assert "Phase 1 of 2 · Without WattNot" in js or "phaseLabel(S.n, brk)" in js
    lib = pathlib.Path(__file__).resolve().parent.parent.joinpath("web/dash/v2lib.js").read_text()
    assert "Phase 1 of 2 · Without WattNot" in lib and "Phase 2 of 2 · With WattNot" in lib


def test_report_has_run_selector_wired_to_mind_map():
    import pathlib
    js = pathlib.Path(__file__).resolve().parent.parent.joinpath("web/dash/v2.js").read_text()
    assert 'id="runSel"' in js and "/api/dash/v2/runs" in js
    assert "selectRun" in js and "headlineFromRecord" in js and "S.selHeadline" in js
    assert "run: S.selRun?.id" in js  # fingerprint stays stable per run


def test_report_is_a_radial_panzoom_canvas():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent.joinpath("web/dash")
    js = root.joinpath("v2.js").read_text()
    css = root.joinpath("v2.css").read_text()
    pan = root.joinpath("pancanvas.js").read_text()
    # pancanvas is vendored in and wired up (plain JS + CSS transform)
    assert 'from "./pancanvas.js"' in js and "initPanCanvas(wrap, stage" in js
    assert "export function init(container, inner" in pan
    for api in ("fit,", "focus,", "setZoom,"):
        assert api in pan
    assert "S.radPan.fit()" in js and ".focus(feat)" in js and ".setZoom(1)" in js
    # centre node opens a run-name dropdown reusing the run list
    assert 'id="centreNode"' in js and ">WattNot</button>" in js
    assert 'id="centreDrop"' in js and "selectRun(b.dataset.run)" in js
    # first-ring cards carry a Saved block (Power/Cost/Water from the run's
    # own WattNot phase vs its baseline phase)
    assert "radSavedLines" in js and "Saved</div>" in js
    for field in ("Power", "Cost", "Water"):
        assert field in js
    assert "phases?.baseline" in js and "phases?.wattnot" in js
    # outer-ring per-method children, with the honesty fallback
    assert "RAD_METHODS" in js and "radMethodSaving" in js
    assert 'RAD_FALLBACK = "not measured in this run"' in js
    assert "rad-child" in js and "radial-wires" in js
    # ring layout that spreads families apart, plus the stacked fallback
    assert "layoutRadial" in js and "drawRadialWires" in js
    assert "0.8 * sector" in js  # children stay inside their own sector
    assert "W < 900" in js and "@media (max-width:900px)" in css
    assert ".radial-centre" in css and ".radial-wires" in css
    # canvas controls: zoom/fit/step come from pancanvas, expand/collapse here
    assert "data-radial" in js and "Expand all" in js and "Collapse all" in js


def test_radial_is_three_branches_for_one_run():
    import pathlib
    js = pathlib.Path(__file__).resolve().parent.parent.joinpath("web/dash/v2.js").read_text()
    assert 'RAD_BRANCHES = ["power", "cost", "water"]' in js
    # branch cards are keyed by metric, not by run: the one-card-per-run
    # ring is gone (the run picker dropdown keeps its own data-run options)
    assert 'data-branch="${id}"' in js
    assert "data-parent" not in js and ".rad-card.featured" not in js
    assert "const n = 3" in js  # three branches, 120 degrees apart
    # every branch opens on its own sub-ring, and Expand all opens all three
    assert '$$("#radialStage .rad-head")' in js
    assert "setCard(c, c.dataset.open" in js
    assert "l.dataset.branch === c.dataset.branch" in js
    assert "k.dataset.branch === c.dataset.branch" in js
    assert '$$("#radialStage .rad-card").forEach((c) => setCard(c, on))' in js
    # the centre picker and #runSel stay in sync both ways
    assert 'id="centreDrop"' in js and 'sel.value = id' in js
    assert '$("#runSel").onchange' in js
    # compact stage that stays readable: small cards, small radii, and the
    # fit zoom never drops below 0.6 (the user pans for the rest)
    assert "R1 = 240" in js
    assert "S.radPan.state.scale < 0.6" in js and "setZoom(0.6)" in js
    css = pathlib.Path(__file__).resolve().parent.parent.joinpath("web/dash/v2.css").read_text()
    assert "width:204px" in css and "width:196px" in css


def test_radial_methods_show_per_metric_saving_or_fallback():
    import pathlib
    js = pathlib.Path(__file__).resolve().parent.parent.joinpath("web/dash/v2.js").read_text()
    assert "radMethodSaving(rec, branch, id)" in js
    # only caps may reuse the whole-run figure, with the no-split note, and
    # cost/water for caps are derived from it with the same note
    assert "Not a per-method split" in js
    assert js.count("splitNote") >= 4  # defined once, reused by power/cost/water
    assert "usd_per_kwh" in js and "factor_l_per_kwh" in js
    assert 'RAD_FALLBACK = "not measured in this run"' in js


def test_power_branch_has_with_without_graph_or_fallback():
    import pathlib
    js = pathlib.Path(__file__).resolve().parent.parent.joinpath("web/dash/v2.js").read_text()
    assert "radPowerSeries" in js and "radPowerSvg" in js
    assert "<svg" in js and "polyline" in js
    assert "j_per_req" in js  # the series comes from the run record itself


def test_run_page_and_before_after_read_model_from_data():
    import pathlib
    js = pathlib.Path(__file__).resolve().parent.parent.joinpath("web/dash/v2.js").read_text()
    assert "Llama 3.1 8B at a 350 W cap, RUN2" not in js  # no hard-coded model line
    assert "const m = S.run2 || {}" in js  # the block reads the run2 payload
    assert "R.results.model" in js  # the header reads the run being shown


def test_numbers_coloured_by_label_with_one_legend_per_view():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent.joinpath("web/dash")
    lib = root.joinpath("v2lib.js").read_text()
    assert "cnum" in lib and "legendHtml" in lib
    assert "badge" in lib and "title=" in lib and "aria-label" in lib
    assert "b-txt" in lib or "b-chip" in lib  # Dusk 2: visible label word next to numbers
    js = root.joinpath("v2.js").read_text()
    assert js.count("legendHtml()") >= 4  # home, configure, run, report
    css = root.joinpath("v2.css").read_text()
    for cls in (".n-measured", ".n-estimate", ".n-simulated", ".n-mock", ".n-replay", ".legend"):
        assert cls in css, cls


def test_partner_tags_are_plain_glass_boxes():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent.joinpath("web/dash")
    css = root.joinpath("v2.css").read_text()
    assert ".ptag" in css and "backdrop-filter" in css
    assert "color:#fff" in css
    js = root.joinpath("v2.js").read_text()
    assert "ptag" in js


def test_bento_shells_with_overlap_and_single_column():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent.joinpath("web/dash")
    css = root.joinpath("v2.css").read_text()
    assert ".bento" in css and "repeat(6,1fr)" in css
    assert ".tile.lift" in css and "margin-top:-10px" in css and "z-index:2" in css
    js = root.joinpath("v2.js").read_text()
    for token in ("bento", "tile glass span4", "span2", "span3", "span6"):
        assert token in js, token


def test_radial_saved_block_reads_phases_and_never_splits():
    c = _c()
    rec = c.get("/api/dash/v2/runs/run2-llama-350w").json()
    b, w = rec["phases"]["baseline"], rec["phases"]["wattnot"]
    assert b["metrics"]["j_per_req"] and w["metrics"]["j_per_req"]
    assert w["metrics"]["saved_pct"] == pytest.approx(-27.1, abs=0.05)
    # the record attributes nothing to single methods, so the canvas must not
    assert "methods" not in rec
    assert all("methods" not in p for p in rec["phases"].values())
    # a fixture run has no measured joules: every Saved line must fall back
    fix = c.get("/api/dash/v2/runs/cloud-recorded-run1").json()
    assert fix["phases"]["baseline"]["metrics"]["j_per_req"] is None
    assert fix["phases"]["wattnot"]["metrics"] is None


def test_landing_page_head_tags_and_content():
    """Minimal one-screen landing page has the required title, meta, og tags, and text-only content."""
    import pathlib
    p = pathlib.Path(__file__).resolve().parent.parent / "web" / "dash" / "index.html"
    assert p.exists()
    html = p.read_text()

    # Head tags & title
    assert "<title>WattNot: cut GPU power per AI inference without losing quality</title>" in html
    assert '<meta name="description"' in html
    assert 'property="og:title" content="WattNot: cut GPU power per AI inference without losing quality"' in html
    assert 'property="og:description"' in html
    assert 'property="og:image"' in html and "hero-1.png" in html
    assert 'property="og:type" content="website"' in html
    assert 'property="og:url" content="https://ak9381.github.io/wattnot/"' in html

    # Headline and body lines
    assert "Was that agent call worth the watts?" in html
    assert "Every watt checked, plus every dollar and litre." in html
    assert "Let WattNot guide your agents" in html
    assert 'href="runs.html"' in html

    # "Powered by" lists the pinned set of Meta tools WattNot actually uses, each
    # rendered as a prominent chip. Muse Image is never listed (no code usage).
    assert "Powered by Meta" in html
    powered_tools = [
        "Muse Spark",
        "Muse Wonder",
        "Muse Glimmer",
        "Muse Code",
        "Voice",
        "Llama",
        "SAM 3.1",
    ]
    for tool in powered_tools:
        assert f'<span class="chip">{tool}</span>' in html
    assert "Muse Image" not in html


def test_configure_stack_step_and_framework_choices():
    """Configure flow has a 'Your stack' step with 4 options and code snippet."""
    import pathlib
    js = pathlib.Path(__file__).resolve().parent.parent / "web" / "dash" / "v2.js"
    text = js.read_text()
    assert "Your stack" in text
    for fw in ("openai-python", "LangChain", "LlamaIndex", "plain API"):
        assert fw in text
    assert 'OpenAI(base_url="http://localhost:8000/v1")' in text
    assert 'ChatOpenAI(base_url="http://localhost:8000/v1")' in text
    assert 'OpenAILike(api_base="http://localhost:8000/v1")' in text
    assert "curl http://localhost:8000/v1/chat/completions" in text


def test_framework_tags_on_dashboard_and_report():
    """Runs dashboard, report hero/receipt, and compare show framework tags."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "web" / "dash"
    runs_js = (root / "runs.js").read_text()
    report_js = (root / "report.js").read_text()
    # v2 reads the framework tag through v2lib.frameworkChip, so the fallback lives there now.
    v2_js = (root / "v2.js").read_text() + (root / "v2lib.js").read_text()

    assert "framework: none recorded" in runs_js
    assert "framework: none recorded" in report_js
    assert "framework: none recorded" in v2_js

