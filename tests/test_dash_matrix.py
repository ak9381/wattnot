"""Run matrix view: 12 PLANNED rounds, grade summary, Meta meter over the 8 models, read-only."""
from __future__ import annotations

import json

from wattnot.dash import matrix


def test_config_has_12_rounds_and_caps():
    cfg = json.loads(matrix.CONFIG.read_text())
    legs = [r["leg"] for r in cfg["rounds"]]
    assert len(legs) == 12 and all(legs.count(k) == 3 for k in ("baseten", "modal", "h100", "combined"))
    assert all(r["arms"] == ["without_wattnot", "with_wattnot"] for r in cfg["rounds"])
    assert cfg["spark_grade_all"] is True and cfg["deterministic_decides"] is True
    b = cfg["meta_budget"]
    assert (b["credits_usd"], b["cap_usd"], b["soft_usd"]) == (150, 120, 100)
    assert b["matrix_estimate_usd"][1] < b["cap_usd"]
    assert {p["id"] for p in cfg["providers"]} == {"baseten", "modal", "e2e"}


def test_build_all_planned_without_results(tmp_path):
    v = matrix.build(root=tmp_path, ledger=tmp_path / "none.json")
    assert len(v["rounds"]) == 12
    assert all(r["status"] == "PLANNED" and r["results"] is None for r in v["rounds"])
    assert len(v["meta"]["per_model"]) == 8 and v["meta"]["total_usd"] == 0
    assert v["meta"]["cap_usd"] == 120 and v["meta"]["soft_usd"] == 100 and v["meta"]["credits_usd"] == 150


def test_build_reads_a_real_round_and_grades(tmp_path):
    d = tmp_path / "out" / "matrix"; d.mkdir(parents=True)
    answers = [{"id": "a", "dataset": "gsm8k", "deterministic": True, "spark": True},
               {"id": "b", "dataset": "gsm8k", "deterministic": True, "spark": False}]
    (d / "r01.json").write_text(json.dumps({"status": "done", "label": "MEASURED",
                                            "results": {"accuracy_with": 0.9}, "answers": answers}))
    r = matrix.build(root=tmp_path, ledger=tmp_path / "none.json")["rounds"][0]
    assert r["status"] == "done" and r["grades"]["agreement"] == 0.5
    assert r["grades"]["correct"] == 2 and r["grades"]["disagreements"][0]["id"] == "b"


def test_meta_meter_splits_by_model(tmp_path):
    m = matrix.meta_meter([{"model": "muse-spark-1.3", "usd": 1.5}, {"model": "muse-spark-1.3-contributor", "usd": 0.5},
                           {"model": "sam-3.1", "usd": 0.01}, {"model": "muse-image-1.0", "usd": 0.02}], 120, 100, 150)
    assert m["per_model"]["muse-spark"]["usd"] == 2.0 and m["per_model"]["sam-3.1"]["calls"] == 1
    assert m["per_model"]["muse-image"]["usd"] == 0.02 and m["total_usd"] == 2.03 and m["warning"] is False


def test_dash_routes_matrix_and_ask_card(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from wattnot.dash.server import create_app
    monkeypatch.setenv("WATTNOT_MATRIX_ROOT", str(tmp_path))
    monkeypatch.setenv("WATTNOT_META_MOCK", "1")
    c = TestClient(create_app())
    v = c.get("/api/dash/matrix").json()
    assert len(v["rounds"]) == 12 and v["rounds"][0]["status"] == "PLANNED"
    assert c.post("/api/dash/ask/card", content="x", headers={"content-type": "text/plain"}).status_code == 415
    j = c.post("/api/dash/ask/card", json={"card_id": "m", "title": "Meta spend", "text": "Meta spend $1 of $120"}).json()
    assert j["label"] == "MOCK" and j["explanation"].startswith("[MOCK]")
