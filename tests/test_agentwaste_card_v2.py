"""The second-layer card: built from the v2 results file, served next to the base card."""
from __future__ import annotations

import json
from unittest import mock

from fastapi.testclient import TestClient

from wattnot.agentwaste.bench.hsummary import summarize
from wattnot.agentwaste.card import card_v2, load_v2
from wattnot.dash.server import create_app


def _rows():
    rows = []
    for cat, cid in (("normal", "normal-react-E1"), ("fuzzy", "fuzzy-1-prompt-drift"),
                     ("retry", "retry-1-status-500")):
        for rep in (0, 1):
            for arm, calls in (("harness", 3 if cat == "normal" else 10),
                               ("harness+saver", 3 if cat == "normal" else 4)):
                miss = arm == "harness" and cat != "normal"
                rows.append({"harness": "react", "case": cid, "category": cat, "arm": arm,
                             "rep": rep, "success": True, "calls": calls,
                             "prompt_tokens": 1000 * calls, "cached_tokens": 0,
                             "completion_tokens": 100 * calls, "reasoning_tokens": 0,
                             "total_tokens": 1100 * calls, "tool_calls": 0, "mutations": 0,
                             "wall_s": calls, "ended_by": "cap" if miss else "final",
                             "miss": miss, "helpers": 0, "helper_caps": 0, "nudges": 0,
                             "stop_reason": None, "flags": {}, "error": None, "said": [],
                             "trace": [{"n": 1, "agent": "main", "text": "t", "calls": [],
                                        "tokens": 5, "note": ""}]})
    return rows


def _results():
    r = summarize(_rows())
    r.update({"reps": 2, "cases_total": 2, "spend_usd_own": 1.0,
              "caps": {"react_max_iterations": 15}})
    return r


def test_card_v2_has_both_tables_categories_and_labels():
    c = card_v2(_results())
    assert c["normal"]["rows"]["calls per run"]["change_pct"] == 0
    assert c["injected"]["rows"]["calls per run"]["change_pct"] < 0
    assert c["normal"]["rows"]["tokens per run"]["tag"] == "MEASURED"
    assert c["injected"]["rows"]["cost per run ($)"]["tag"] == "ESTIMATE"
    assert c["injected"]["rows"]["energy per run (J)"]["tag"] == "ESTIMATE"
    assert [x["id"] for x in c["categories"]] == ["fuzzy", "retry"]
    assert c["injected"]["miss_runs"] == {"off": 4, "on": 0}
    assert "not the" in " ".join(c["notes"]) and "real libraries" in " ".join(c["notes"])
    assert c["spend_usd"] == 1.0
    assert c["cases"] == 2 and c["normal_cases"] == 1


def test_load_v2_refuses_other_schemas_and_bad_files(tmp_path):
    assert load_v2(tmp_path / "nope.json") is None
    p = tmp_path / "r.json"
    p.write_text("{bad")
    assert load_v2(p) is None
    p.write_text(json.dumps({"schema": "other"}))
    assert load_v2(p) is None
    p.write_text(json.dumps(_results()))
    assert load_v2(p)["schema"] == "wattnot.agent-waste-v2/1"


def test_endpoint_adds_second_layer_only_when_results_exist():
    with mock.patch("wattnot.dash.server.load_waste_v2", return_value=_results()):
        body = TestClient(create_app()).get("/api/dash/agent-waste").json()
    assert body["second_layer"]["injected"]["rows"]["calls per run"]["off"] == 10
    assert len(body["notes"]) == 3  # the base card is unchanged
    with mock.patch("wattnot.dash.server.load_waste_v2", return_value=None):
        body = TestClient(create_app()).get("/api/dash/agent-waste").json()
    assert "second_layer" not in body
