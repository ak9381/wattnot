"""The after-the-fixes card: v2 saver vs v3 saver, built from the v3 results file."""
from __future__ import annotations

import json
from unittest import mock

from fastapi.testclient import TestClient

from wattnot.agentwaste.card import card_v3, load_v3
from wattnot.dash.server import create_app


def _arm(calls, ok=2, runs=2):
    return {"runs": runs, "success": ok, "calls_per_run": calls, "tokens_per_run": calls * 1000,
            "miss_runs": 0, "wall_s_per_run": 1.0}


def _blk(h, s):
    return {"harness": _arm(h), "harness+saver": _arm(s)}


def _res():
    return {"schema": "wattnot.agent-waste-v3/1", "reps": 2, "spend_usd_own": 0.98,
            "generated": "2026-10-27T00:00:00Z", "fixes": ["a", "b"], "notes": ["n1"],
            "normal": _blk(3, 3), "injected": _blk(15, 5),
            "cases": {"normal-1": {"category": "normal", **_blk(3, 3)},
                      "noprog-3": {"category": "noprog", **_blk(15, 3)}},
            "before": {"normal": _blk(3, 3), "injected": _blk(15, 6),
                       "cases": {"noprog-3": {"category": "noprog", **_blk(15, 3)}}}}


def test_card_v3_lists_fixed_cases_and_both_savers():
    c = card_v3(_res())
    assert [x["id"] for x in c["cases"]] == ["noprog-3"]
    assert c["fixed"]["saver_v3"]["calls"] == 5
    assert c["fixed"]["saver_v2"]["calls"] == 6
    assert c["fixed"]["harness"]["calls"] == 15
    assert c["normal"]["saver_v3"]["runs"] == 2
    assert c["fixes"] == ["a", "b"] and c["notes"] == ["n1"]
    assert c["spend_usd"] == 0.98 and c["reps"] == 2


def test_card_v3_survives_missing_before():
    r = _res()
    r.pop("before")
    c = card_v3(r)
    assert c["fixed"]["saver_v2"]["calls"] is None or not c["fixed"]["saver_v2"].get("runs")


def test_load_v3_checks_schema(tmp_path):
    assert load_v3(tmp_path / "none.json") is None
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"schema": "other"}))
    assert load_v3(p) is None
    p.write_text(json.dumps(_res()))
    assert load_v3(p)["schema"] == "wattnot.agent-waste-v3/1"


def test_endpoint_attaches_after_fixes_only_with_both_results():
    from tests.test_agentwaste_card_v2 import _results as v2_results
    with mock.patch("wattnot.dash.server.load_waste_v2", return_value=v2_results()), \
         mock.patch("wattnot.dash.server.load_waste_v3", return_value=_res()):
        body = TestClient(create_app()).get("/api/dash/agent-waste").json()
    assert body["second_layer"]["after_fixes"]["fixed"]["saver_v3"]["calls"] == 5
    with mock.patch("wattnot.dash.server.load_waste_v2", return_value=v2_results()), \
         mock.patch("wattnot.dash.server.load_waste_v3", return_value=None):
        body = TestClient(create_app()).get("/api/dash/agent-waste").json()
    assert "after_fixes" not in body["second_layer"]
    with mock.patch("wattnot.dash.server.load_waste_v2", return_value=None), \
         mock.patch("wattnot.dash.server.load_waste_v3", return_value=_res()):
        body = TestClient(create_app()).get("/api/dash/agent-waste").json()
    assert "second_layer" not in body
