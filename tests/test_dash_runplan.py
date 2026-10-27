"""The config -> run -> results flow: limits are re-evaluated from recorded numbers, events are valid
wattnot.event/1, every run is labelled REPLAY, and nothing is passed off as live."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wattnot.dash import runplan
from wattnot.dash.server import create_app

ROOT = Path(__file__).resolve().parents[1]
STORY = ROOT / "data" / "dash" / "story.json"
SCHEMA = ROOT / "schemas" / "run-event.schema.json"
pytestmark = pytest.mark.skipif(not STORY.is_file(), reason="story bundle not built")


@pytest.fixture(scope="module")
def bundle():
    return json.loads(STORY.read_text())


def models(b):
    return [m["id"] for m in b["config"]["models"]]


def test_every_model_plans_and_every_step_exists_in_the_pipeline(bundle):
    for m in models(bundle):
        p = runplan.plan(bundle, {"model": m})
        assert p["source"] == "REPLAY"
        subs = {n["id"]: {s["id"] for s in n["subs"]} for n in p["pipeline"]}
        for f in p["events"]:
            d = f["event"]["data"]
            if "node" in d:
                assert d["sub"] in subs[d["node"]], (m, d["node"], d["sub"])


def test_events_follow_the_run_event_schema(bundle):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text())
    for m in models(bundle):
        p = runplan.plan(bundle, {"model": m})
        seqs = []
        for f in p["events"]:
            jsonschema.validate(f["event"], schema)
            seqs.append(f["event"]["seq"])
        assert seqs == list(range(len(seqs)))
        assert p["events"][0]["event"]["type"] == "run.started"
        assert p["events"][-1]["event"]["type"] == "run.finished"
        assert p["events"][0]["event"]["data"]["replay"] is True


def test_every_run_says_replay_and_every_step_has_plain_words(bundle):
    for m in models(bundle):
        p = runplan.plan(bundle, {"model": m})
        assert p["label"] == "REPLAY" and p["results"]["source"] == "REPLAY"
        for f in p["events"]:
            e = f["event"]
            if e["type"] == "step.started":
                n = e["data"]["narration"]
                assert n["text"] and n["source"] == "template"
            for nm in e["data"].get("numbers", []):
                assert nm["tag"] in (None, "MEASURED", "ESTIMATE", "SIMULATED", "REPLAY", "ADVICE")
                assert nm["v"] is not None or nm["tag"] is None


def test_cap_limit_picks_the_deepest_cap_inside_the_p99_limit(bundle):
    wide = runplan.plan(bundle, {"model": "glimmer", "p99_slack_pct": 10})["results"]["levers"][0]
    assert wide["applied"] and wide["after_label"] == "400 W"
    tight = runplan.plan(bundle, {"model": "glimmer", "p99_slack_pct": 0})["results"]["levers"][0]
    assert tight["p99_after_s"] <= tight["p99_before_s"]          # nothing slower than stock may be kept
    assert tight["saved_pct"] <= wide["saved_pct"]
    cap = {t["cap"]: t for t in wide["tried"]}
    assert cap[400]["p99_ok"] and wide["p99_change_pct"] == cap[400]["p99_change_pct"]


def test_cap_quality_is_never_called_passed(bundle):
    for strict, applied in ((False, True), (True, False)):
        lv = runplan.plan(bundle, {"model": "glimmer", "strict_quality": strict})["results"]["levers"][0]
        assert lv["quality"]["state"] == "pending" and lv["applied"] is applied


def test_topk_gate_follows_the_agreement_limit(bundle):
    q = runplan.plan(bundle, {"model": "qwen3"})["results"]["levers"][0]
    g95 = runplan.plan(bundle, {"model": "granite", "min_quality_pct": 95})["results"]["levers"][0]
    g94 = runplan.plan(bundle, {"model": "granite", "min_quality_pct": 94})["results"]["levers"][0]
    assert q["applied"] and q["quality"]["state"] == "passed"
    assert not g95["applied"] and g95["quality"]["state"] == "failed" and g95["note"].startswith("Tried and not kept")
    assert g94["applied"] and g94["saved_pct"] > 5


def test_levers_are_not_summed_and_unsupported_levers_are_dropped(bundle):
    p = runplan.plan(bundle, {"model": "spark_open", "levers": ["effort", "cache", "cap", "topk"]})
    ids = [lv["id"] for lv in p["results"]["levers"]]
    assert ids == ["effort", "cache"]
    assert "never" in p["results"]["levers"][1]["note"].lower() or "not added" in p["results"]["levers"][1]["note"]
    with pytest.raises(ValueError):
        runplan.plan(bundle, {"model": "glimmer", "levers": ["effort"]})
    with pytest.raises(ValueError):
        runplan.plan(bundle, {"model": "nope"})


def test_water_and_cost_use_the_cited_factors(bundle):
    lv = runplan.plan(bundle, {"model": "glimmer"})["results"]["levers"][0]
    assert lv["litres_before"] == pytest.approx(lv["wh_before"] / 1000 * 0.36)
    assert lv["usd_before"] == pytest.approx(lv["wh_before"] / 1000 * 0.1387)
    assert "Lawrence Berkeley" in bundle["config"]["water"]["source"]


def test_complexity_comes_from_recorded_numbers_and_missing_stays_missing(bundle):
    for m in models(bundle):
        for i in bundle["config"]["complexity"][m]["items"]:
            assert i["tag"] is None or i["v"] is not None
    ctx = bundle["config"]["complexity"]["glimmer"]["items"]
    assert next(i for i in ctx if i["k"] == "conc")["v"] == 32


def test_server_plan_endpoint_and_errors():
    c = TestClient(create_app())
    r = c.post("/api/dash/run/plan", json={"model": "qwen3"})
    assert r.status_code == 200 and r.json()["label"] == "REPLAY"
    assert c.post("/api/dash/run/plan", json={"model": "x"}).status_code == 400
    assert c.post("/api/dash/run/plan", content=b"nope").status_code == 400
    assert c.post("/api/dash/run/plan", json=[1]).status_code == 400
    assert c.get("/api/dash/health").json()["events_name"] is None


# ---------------------------------------------------------------- the pipeline and the room scan
from wattnot.dash import pipeline  # noqa: E402

SRC = ROOT / "src" / "wattnot"


def test_the_run_graph_is_the_eight_pipeline_steps_in_order(bundle):
    ids = [n["id"] for n in runplan.plan(bundle, {"model": "glimmer"})["pipeline"]]
    assert ids == ["room", "baseline", "gate", "sweep", "waste", "approve", "keep", "receipt"]
    ids = [n["id"] for n in runplan.plan(bundle, {"model": "qwen3"})["pipeline"]]
    assert ids == ["room", "baseline", "gate", "topk", "waste", "approve", "keep", "receipt"]
    assert [n["id"] for n in bundle["config"]["pipeline"]] == ["room", "baseline", "gate", "sweep", "topk", "waste", "approve", "keep", "receipt"]
    assert [n["id"] for n in runplan.plan(bundle, {"model": "glimmer", "room": "none"})["pipeline"]][0] == "baseline"


def test_the_waste_stage_names_meta_and_real_code(bundle):
    p = runplan.plan(bundle, {"model": "glimmer"})
    n = next(x for x in p["pipeline"] if x["id"] == "waste")
    assert n["title"] == "Agent waste" and n["partners"] == ["meta"]
    assert n["meta_product"] == "Muse Spark" and n["code"] == "agentwaste/guard.py"
    subs = {s["id"]: s for s in n["subs"]}
    assert set(subs) == {"watch", "detect", "nudge", "budget", "exit"}
    assert subs["watch"]["partner"] == "meta" and subs["exit"]["event"] == "decision"
    for s in n["subs"]:
        assert (SRC / s["code"]).is_file()


def test_the_waste_steps_run_before_keep_with_numbers_from_the_benchmark(bundle):
    for m in models(bundle):
        p = runplan.plan(bundle, {"model": m})
        if m == "spark_open":
            continue  # effort and cache levers share the hosted run; checked below
        started = [f["event"] for f in p["events"] if f["event"]["type"] == "step.started"]
        subs = [e["data"]["sub"] for e in started if e["data"].get("node") == "waste"]
        assert subs == ["watch", "detect", "nudge", "budget", "exit"], m
        nodes = [f["event"]["data"].get("node") for f in p["events"] if f["event"]["type"] == "step.started"]
        assert nodes.index("waste") > nodes.index("sweep" if "sweep" in nodes else "topk")
        assert nodes.index("waste") < nodes.index("keep")
        for e in started:
            if e["data"].get("node") == "waste":
                assert e["data"]["narration"]["source"] == "template" and e["data"]["narration"]["text"]
        nums = [n for f in p["events"] if f["event"]["type"] == "step.finished"
                and f["event"]["data"].get("node") == "waste" for n in f["event"]["data"]["numbers"]]
        assert nums and all(n["tag"] in ("MEASURED", "ESTIMATE") and n["v"] is not None for n in nums)
        dec = [f["event"]["data"] for f in p["events"] if f["event"]["type"] == "decision"
               and f["event"]["data"].get("kind") == "agent_waste"]
        assert len(dec) == 1 and dec[0]["stopped"] is True and dec[0]["reason"] == "done"
        assert dec[0]["calls_on"] is not None and dec[0]["success_on_pct"] is not None


def test_the_hosted_run_also_carries_the_waste_steps(bundle):
    p = runplan.plan(bundle, {"model": "spark_open"})
    subs = [f["event"]["data"]["sub"] for f in p["events"]
            if f["event"]["type"] == "step.started" and f["event"]["data"].get("node") == "waste"]
    assert subs == ["watch", "detect", "nudge", "budget", "exit"]


def test_the_waste_steps_show_dashes_without_a_results_file(bundle, monkeypatch):
    monkeypatch.setattr(runplan, "load_waste", lambda: None)
    p = runplan.plan(bundle, {"model": "glimmer"})
    fin = [f["event"] for f in p["events"] if f["event"]["type"] == "step.finished"
           and f["event"]["data"].get("node") == "waste"]
    assert len(fin) == 5
    for e in fin:
        assert e["data"]["label"] is None
        for n in e["data"]["numbers"]:
            assert n["v"] is None and n["tag"] is None
    assert p["events"][0]["event"]["data"]["label"] == "REPLAY"


def _code_defined(path, symbol):
    import re
    text = (SRC / path).read_text()
    return all(re.search(rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(part)}\b|^\s*{re.escape(part)}\s*[:=]", text, re.M)
               for part in symbol.split("."))


def test_pipeline_code_paths_are_real_files(bundle):
    for n in pipeline.full_nodes():
        assert (SRC / n["code"]).is_file(), n["code"]
        for s in n["subs"]:
            path, _, sym = s["code"].partition("::")
            assert (SRC / path).is_file(), (n["id"], s["id"], s["code"])
            if sym:
                assert _code_defined(path, sym), (n["id"], s["id"], s["code"])


def test_live_event_map_points_at_real_steps():
    subs = {n["id"]: {s["id"] for s in n["subs"]} for n in pipeline.full_nodes()}
    for typ, (node, sub) in pipeline.PIPE_MAP.items():
        assert sub in subs[node], typ


def test_room_scan_is_first_and_only_the_gpu_power_is_measured(bundle):
    p = runplan.plan(bundle, {"model": "glimmer"})
    steps = [f["event"]["data"] for f in p["events"] if f["event"]["type"] == "step.started"]
    assert [d["sub"] for d in steps[:5]] == ["segment", "heat", "xcheck", "rank", "pick"] and steps[0]["node"] == "room"
    scan = p["room"]["scan"]
    assert scan["schema"] == "wattnot.room/1" and scan["label"] == "SIMULATED" and scan["source"] == "stub"
    for r in scan["racks"]:
        assert r["temp_label"] == "SIMULATED"
        assert r["power_label"] == ("MEASURED" if r["gpu"] else "SIMULATED")
    tgt = next(r for r in scan["racks"] if r["id"] == p["room"]["target"])
    assert tgt["gpu"] and tgt["decision"] == "act" and tgt["power_w"] > 100
    hottest = max(scan["racks"], key=lambda r: r["temp_c"])
    assert hottest["id"] != tgt["id"] and hottest["decision"] == "watch"     # the hottest rack has no card access
    dec = [f["event"]["data"] for f in p["events"] if f["event"]["type"] == "decision"]
    assert dec[0]["choice"]["racks"] == [tgt["id"]] and dec[0]["model"] is None


def test_actions_are_tagged_with_the_rack_and_the_room_cools_only_there(bundle):
    p = runplan.plan(bundle, {"model": "glimmer"})
    for f in p["events"]:
        d = f["event"]["data"]
        if d.get("node") in ("baseline", "sweep", "keep"):
            assert d["rack"] == p["room"]["target"]
        if d.get("node") == "room" and f["event"]["type"] == "step.started":
            assert "rack" not in d
    b, a = p["room"]["scan"]["racks"], p["room"]["after"]["racks"]
    changed = [x["id"] for x, y in zip(b, a, strict=True) if x["temp_c"] != y["temp_c"]]
    assert changed == [p["room"]["target"]]
    assert p["results"]["room"]["after"]["label"] == "SIMULATED"
    assert p["results"]["room"]["cooling"]["value"] == 1.56
    off = runplan.plan(bundle, {"model": "glimmer", "room": "none"})
    assert off["room"] is None and "room" not in off["results"]
    assert not any("rack" in f["event"]["data"] for f in off["events"])


def test_hosted_runs_have_no_room(bundle):
    p = runplan.plan(bundle, {"model": "spark_open", "room": "example"})
    assert p["room"] is None and p["pipeline"][0]["id"] == "baseline"


def test_room_scan_interface_accepts_a_module_scan_and_rejects_bad_ones(bundle):
    scan = pipeline.example_room(500.0)
    scan["source"] = "sam"
    p = runplan.plan(bundle, {"model": "glimmer", "room_scan": scan})
    assert p["room"]["scan"]["source"] == "sam"
    bad = pipeline.example_room()
    bad["racks"][0]["polygon"] = [[0, 0]]
    with pytest.raises(ValueError):
        runplan.plan(bundle, {"model": "glimmer", "room_scan": bad})
    bad = pipeline.example_room()
    bad["racks"][1]["decision"] = "delete everything"
    with pytest.raises(ValueError):
        pipeline.validate_room_scan(bad)
    with pytest.raises(ValueError):
        pipeline.validate_room_scan({"schema": "other", "racks": []})


def test_replay_runs_approve_before_keep(bundle):
    for mid in ("glimmer", "qwen3"):
        p = runplan.plan(bundle, {"model": mid})
        kinds = [(f["event"]["data"].get("node"), f["event"]["data"].get("sub"))
                 for f in p["events"] if f["event"]["type"] == "step.started"]
        rel = [(n, s) for n, s in kinds if n in ("approve", "keep")]
        assert [s for n, s in rel if n == "approve"] == ["classify", "decide"], mid
        first_ok = next(i for i, (n, s) in enumerate(rel) if n == "approve")
        first_keep = next(i for i, (n, s) in enumerate(rel) if n == "keep")
        assert first_ok < first_keep
        assert any("auto-approved in replay (--auto-approve), logged as such" in
                   (f["event"]["data"].get("summary") or "").lower()
                   for f in p["events"] if f["event"]["type"] == "step.finished"), mid
    # a run that keeps nothing has no classify, only a decide with nothing to approve
    p = runplan.plan(bundle, {"model": "granite", "min_quality_pct": 95})
    subs = [f["event"]["data"].get("sub") for f in p["events"]
            if f["event"]["type"] == "step.started" and f["event"]["data"].get("node") == "approve"]
    assert subs == ["decide"]
    assert any("nothing to approve" in (f["event"]["data"].get("outputs") or "")
               for f in p["events"] if f["event"]["type"] == "step.finished")


def test_approve_step_numbers_carry_risk_and_replay_labels():
    from datetime import UTC, datetime
    S = runplan._Seq("abcdef", datetime.now(UTC))
    runplan._approve_cap_change(S, {"cap": 400})
    nums = [n for f in S.frames for n in f["event"]["data"].get("numbers", [])]
    assert {n["label"]: n["tag"] for n in nums} == {
        "risk level": "ADVICE", "cap vs stock": "REPLAY", "cap vs TDP": "REPLAY"}
    assert any("no operator" in f["event"]["data"]["narration"]["text"]
               for f in S.frames if f["event"]["type"] == "step.started")


def test_after_room_changes_nothing_when_power_is_unknown():
    scan = pipeline.example_room(None)
    tgt = pipeline.target_of(scan)
    assert tgt["power_w"] is None and tgt["power_label"] is None
    after = pipeline.after_room(scan, tgt, None, None)
    assert [r["temp_c"] for r in after["racks"]] == [r["temp_c"] for r in scan["racks"]]


def test_nodes_carry_powered_by():
    by = {n["id"]: n["powered_by"] for n in pipeline.nodes("cap", True)}
    assert by["room"] == [{"id": "meta", "name": "Meta SAM 3.1",
                           "what": "finds the racks"}]
    assert by["approve"] == [{"id": "operator", "name": "Operator",
                              "what": "a person decides"}]
    assert by["gate"] == [] and by["receipt"] == []
    assert by["baseline"] == [{"id": "e2e", "name": "E2E H100",
                               "what": "the GPU it runs on"}]
    assert by["keep"] == [{"id": "e2e", "name": "E2E H100",
                           "what": "the GPU it runs on"}]
    assert by["sweep"][0] == {"id": "e2e", "name": "E2E H100",
                              "what": "the GPU it runs on"}
    assert by["sweep"][1] == {"id": "planner", "name": "planner",
                              "what": "proposes the next cap"}
    topk = {n["id"]: n["powered_by"] for n in pipeline.nodes("topk", True)}
    assert topk["topk"] == [{"id": "e2e", "name": "E2E H100",
                             "what": "the GPU it runs on"}]


def test_hosted_nodes_run_on_baseten():
    by = {n["id"]: n["powered_by"] for n in pipeline.nodes("hosted", False)}
    assert by["baseline"][0]["id"] == "baseten"
    assert by["baseline"][0]["name"] == "Baseten Model API"
    assert by["sweep"][0]["id"] == "baseten"
    assert by["sweep"][-1]["id"] == "planner"
    assert by["approve"] == [{"id": "operator", "name": "Operator",
                              "what": "a person decides"}]


def test_powered_by_entries_are_labelled_words_not_numbers():
    for n in pipeline.full_nodes():
        assert "powered_by" in n
        for entry in n["powered_by"]:
            assert set(entry) == {"id", "name", "what"}
            assert all(isinstance(v, str) and v for v in entry.values())
