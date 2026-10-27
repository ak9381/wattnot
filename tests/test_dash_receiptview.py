"""The Results screen's receipt block: a rehearsal-shaped receipt maps to a trimmed view."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from wattnot.dash import receiptview
from wattnot.dash.arch import PRICE, WATER
from wattnot.dash.server import create_app


def step(cap, accepted, by, stock=(0.68, 0.68), capped=(0.60, 0.60)):
    return {"cap_w": cap, "accepted": accepted, "why_not": [], "label": "MOCK",
            "j_per_req_stock": list(stock), "j_per_req_cap": list(capped),
            "energy_change_pct": {"mean": -11.8, "sd": 0.0},
            "p99_change_pct": {"mean": 5.8, "sd": 0.0},
            "p99_s": {"stock": 1.04, "cap": 1.10},
            "quality": {"stock_pct": 100.0, "cap_pct": 100.0, "passed": True,
                        "reason": "pass rate 100.0% vs 100.0%"},
            "chosen_by": by, "planner_reason": "p99 has headroom; start in the middle",
            "blocks": [{"prompt": "do not leak me", "reply": "x"}]}


def receipt(**kw):
    doc = {"spec": "wattnot.proof-receipt/1", "receipt_id": "proof-abc123",
           "run_id": "run_20260101_000000_abcdef", "created": "2026-01-01T00:00:00",
           "label": "MOCK", "mode": "rehearsal", "model": "meta-llama/Llama-3.1-8B-Instruct",
           "best_cap_w": 400, "planner": {"name": "muse-spark", "model": "muse-spark-1.3"},
           "steps": [step(500, True, "muse-spark"), step(400, True, "grid")],
           "integrity": {"sha256": "6f" * 32, "signer": "offline:abc123", "rekor": None}}
    return {**doc, **kw}


def test_view_maps_before_after_labels_hash_and_steps():
    v = receiptview.view(receipt())
    assert v["kept"] and v["kept_cap_w"] == 400
    assert v["energy_j_per_req"] == {"before": 0.68, "after": 0.60,
                                    "saved_pct": -11.8, "label": "MOCK"}
    assert v["cost_usd_per_1m"]["before"] == pytest.approx(0.68 / 3.6 * PRICE["usd_per_kwh"])
    assert v["cost_usd_per_1m"]["after"] == pytest.approx(0.60 / 3.6 * PRICE["usd_per_kwh"])
    assert v["cost_usd_per_1m"]["label"] == "ESTIMATE" and v["cost_usd_per_1m"]["formula"]
    assert v["water_l_per_1m"]["before"] == pytest.approx(0.68 / 3.6 * WATER["factor_l_per_kwh"])
    assert v["water_l_per_1m"]["label"] == "ESTIMATE"
    assert v["p99_s"] == {"before": 1.04, "after": 1.10, "change_pct": 5.8, "label": "MOCK"}
    assert v["quality_pct"] == {"stock": 100.0, "cap": 100.0, "kept": True, "label": "MOCK"}
    assert v["hash"] == "6f" * 32
    assert v["signature"] == {"signed": True, "signer": "offline:abc123", "rekor": None}
    assert v["steps"] == [
        {"cap_w": 500, "accepted": True, "label": "MOCK", "chosen_by": "muse-spark"},
        {"cap_w": 400, "accepted": True, "label": "MOCK", "chosen_by": "grid"}]
    assert "prompt" not in json.dumps(v) and "blocks" not in json.dumps(v)


def test_view_with_nothing_kept_keeps_stock_and_marks_it():
    v = receiptview.view(receipt(best_cap_w=None,
                                 steps=[step(500, False, "muse-spark")]))
    assert not v["kept"] and v["kept_cap_w"] is None
    assert v["energy_j_per_req"]["before"] == v["energy_j_per_req"]["after"] == 0.68
    assert v["energy_j_per_req"]["saved_pct"] == 0.0


def test_load_rejects_wrong_spec_and_missing_hash(tmp_path):
    good = tmp_path / "proof-x.json"
    good.write_text(json.dumps(receipt()))
    assert receiptview.load_receipt(good)["receipt_id"] == "proof-abc123"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(receipt(spec="other/1")))
    with pytest.raises(ValueError):
        receiptview.load_receipt(bad)
    bad.write_text(json.dumps(receipt(integrity={"signer": "offline:x"})))
    with pytest.raises(ValueError):
        receiptview.load_receipt(bad)
    bad.write_text("{nope")
    with pytest.raises(ValueError):
        receiptview.load_receipt(bad)


def test_find_receipt_prefers_receipt_json_then_newest_proof(tmp_path):
    ev = tmp_path / "run.events.jsonl"
    ev.write_text("{}\n")
    with pytest.raises(FileNotFoundError):
        receiptview.find_receipt(ev)
    (tmp_path / "proof-aaa.json").write_text(json.dumps(receipt()))
    (tmp_path / "proof-zzz.json").write_text(json.dumps(receipt()))
    assert receiptview.find_receipt(ev).name == "proof-zzz.json"
    (tmp_path / "receipt.json").write_text(json.dumps(receipt()))
    assert receiptview.find_receipt(ev).name == "receipt.json"


def test_find_receipt_climbs_to_the_run_root(tmp_path):
    run = tmp_path / "o" / "runs" / "run_x"
    run.mkdir(parents=True)
    ev = run / "events.jsonl"
    ev.write_text("{}\n")
    with pytest.raises(FileNotFoundError):
        receiptview.find_receipt(ev)
    (tmp_path / "o" / "proof-x.json").write_text(json.dumps(receipt()))
    assert receiptview.find_receipt(ev).name == "proof-x.json"


def test_endpoint_serves_the_trimmed_view_and_404s_without_one(tmp_path):
    run = tmp_path / "run1"
    run.mkdir()
    ev = run / "run.events.jsonl"
    ev.write_text("{}\n")
    c = TestClient(create_app(events=ev))
    assert c.get("/api/dash/run/receipt").status_code == 404
    (run / "proof-abc123.json").write_text(json.dumps(receipt()))
    r = c.get("/api/dash/run/receipt")
    assert r.status_code == 200
    v = r.json()
    assert v["hash"] == "6f" * 32 and v["kept_cap_w"] == 400
    assert v["signature"]["signed"] is True
    assert "prompt" not in r.text  # no prompt text leaves the server
    assert TestClient(create_app()).get("/api/dash/run/receipt").status_code == 404
    assert TestClient(create_app(events=ev, replay_only=True)).get(
        "/api/dash/run/receipt").status_code == 200


def test_find_receipt_skips_the_signature_bundle(tmp_path):
    (tmp_path / "proof-abc123.json").write_text(json.dumps(receipt()))
    (tmp_path / "proof-abc123.sigstore.json").write_text("{}")
    run = tmp_path / "runs" / "run_1"
    run.mkdir(parents=True)
    events = run / "events.jsonl"
    events.write_text("")
    assert receiptview.find_receipt(events).name == "proof-abc123.json"


def test_rehearsal_receipt_numbers_are_labelled_mock():
    doc = receipt(mode="rehearsal", label="MOCK")
    doc["steps"][1]["label"] = "MEASURED"
    v = receiptview.view(doc)
    assert v["energy_j_per_req"]["label"] == "MOCK" and v["p99_s"]["label"] == "MOCK"
