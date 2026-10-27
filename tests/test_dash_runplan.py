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


def test_every_model_plans_and_every_step_exists_in_the_map(bundle):
    subs = {n["id"]: {s["id"] for s in n["subs"]} for n in bundle["nodes"]}
    for m in models(bundle):
        p = runplan.plan(bundle, {"model": m})
        assert p["source"] == "REPLAY"
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
                assert nm["tag"] in (None, "MEASURED", "ESTIMATE")
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
