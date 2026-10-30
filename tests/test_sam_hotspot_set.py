"""SAM synthetic hotspot set (the detector self-test): asset loading, IoU
matcher, the gate, its place in the keep/reject loop, the receipt block, and
the --fake runner. Everything here is SYNTHETIC, never a real measurement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wattnot.events.emitter import Emitter
from wattnot.gates import decide, guarded_apply
from wattnot.receipt import build_receipt, verify_hash
from wattnot.runner.trial import TrialResult
from wattnot.runner.vllm_server import TrialParams
from wattnot.runner.workload import WorkloadItem
from wattnot.sam import (
    FAULTS,
    grade_hotspots,
    hotspot_set_gate,
    load_hotspot_set,
    run_hotspot_set,
)
from wattnot.sam.hotspot_set import (
    OfflineDetector,
    detect_hotspots,
    main as hotspot_main,
    score_iou,
)

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "receipts"
RUN = "run_20261030_000000_abcdef"


def _detected(hset) -> dict:
    """Run the offline detector over the whole set (every scene a hit)."""
    masks, _ = detect_hotspots(hset, OfflineDetector())
    return masks


# --- assets -----------------------------------------------------------------

def test_assets_load_and_are_synthetic():
    hset = load_hotspot_set(require_files=True)
    assert len(hset) >= 4
    assert hset.label == "SYNTHETIC"
    assert all(sc.label == "SYNTHETIC" for sc in hset)
    # Every committed placeholder image exists next to the manifest.
    assert all(sc.path(hset.base_dir).exists() for sc in hset)
    faults = {sc.fault for sc in hset}
    assert set(FAULTS) <= faults
    assert all(sc.fault in FAULTS for sc in hset)


def test_rejects_non_synthetic_manifest(tmp_path):
    (tmp_path / "scenes.json").write_text(json.dumps(
        {"label": "REAL", "scenes": []}))
    with pytest.raises(ValueError):
        load_hotspot_set(tmp_path)


# --- matcher / gate ---------------------------------------------------------

def test_score_iou_perfect_mask():
    hset = load_hotspot_set()
    sc = hset.scenes[0]
    mask = OfflineDetector()(sc)
    assert score_iou(mask, sc) == pytest.approx(1.0)


def test_gate_passes_when_all_detected():
    hset = load_hotspot_set()
    masks = _detected(hset)
    gr = hotspot_set_gate(masks, hset.scenes)
    assert gr.passed
    assert gr.detail["missed"] == []
    assert gr.detail["passed_count"] == gr.detail["checked"]
    assert gr.detail["blocks_musecode_pr"] is False
    assert gr.detail["label"] == "SYNTHETIC"
    # Every scene carries an ESTIMATE energy stamp.
    assert all(row["energy_wh"]["label"] == "ESTIMATE" for row in gr.detail["scenes"])


def test_gate_catches_a_miss():
    hset = load_hotspot_set()
    masks = _detected(hset)
    missed_id = hset.scenes[0].id
    masks[missed_id] = {}  # a blank mask is a miss
    gr = hotspot_set_gate(masks, hset.scenes)
    assert not gr.passed
    assert missed_id in gr.detail["missed"]
    assert gr.detail["blocks_musecode_pr"] is True
    assert gr.gate == "hotspot_set"


def test_zero_budget_detects_nothing():
    hset = load_hotspot_set()
    masks, meta = detect_hotspots(hset, OfflineDetector(), budget_s=0.0)
    assert meta["budget_hit"] and meta["detected"] == 0
    gr = hotspot_set_gate(masks, hset.scenes)
    assert not gr.passed
    assert sorted(gr.detail["missed"]) == sorted(hset.ids)


# --- keep/reject loop -------------------------------------------------------

def _trial(tid: str) -> TrialResult:
    return TrialResult(tid, TrialParams(), (), 20, 0.01, 1.0, 0.5, 0.9, 1.0)


def _items() -> list[WorkloadItem]:
    return [WorkloadItem("a", "x", 8, {"checks": [{"type": "exact", "value": "42"}]})]


def test_hotspot_failure_rolls_back_to_last_safe_config(tmp_path):
    hset = load_hotspot_set()
    masks = _detected(hset)
    del masks[hset.scenes[0].id]  # a dropped mask is a miss
    gr = hotspot_set_gate(masks, hset.scenes)
    calls = []
    with Emitter(RUN, root=tmp_path) as em:
        d = decide(_trial("b"), _trial("t1"), {"a": "42"}, {"a": "42"}, _items(),
                   hotspot=gr, emit=em)
        ok = guarded_apply(d, apply=lambda: calls.append("apply"),
                           revert=lambda: calls.append("revert"),
                           change={"power_cap_w": 300}, emit=em)
    assert not ok and calls == ["revert"]
    assert "hotspot_set" in d.failed
    ev = [json.loads(x) for x in (tmp_path / "runs" / RUN / "events.jsonl").read_text().splitlines()]
    gates = [e["data"]["gate"] for e in ev if e["type"] == "gate.result"]
    assert "hotspot_set" in gates
    assert ev[-1]["type"] == "action.rolled_back"
    assert "hotspot_set" in ev[-1]["data"]["failed_gates"]


def test_hotspot_pass_keeps_change(tmp_path):
    hset = load_hotspot_set()
    gr = hotspot_set_gate(_detected(hset), hset.scenes)
    calls = []
    with Emitter(RUN, root=tmp_path) as em:
        d = decide(_trial("b"), _trial("t1"), {"a": "42"}, {"a": "42"}, _items(),
                   hotspot=gr, emit=em)
        guarded_apply(d, apply=lambda: calls.append("apply"),
                      revert=lambda: calls.append("revert"), change={}, emit=em)
    assert d.passed and calls == ["apply"]


# --- receipt ----------------------------------------------------------------

@pytest.fixture
def base_run():
    valid = json.loads((FIXTURES / "valid.json").read_text())
    return {
        "run_id": "run_20261030_150000_hottest",
        "subject": valid["subject"],
        "baseline": valid["baseline"],
        "candidate": valid["candidate"],
        "measurement": {
            "meter": "NVML internal energy counter via pynvml", "hz": 10.0,
            "label": "MEASURED",
            "csv": [{"role": "candidate", "path": "cand.csv", "sha256": "0" * 64}],
        },
        "planner_trace": valid["planner_trace"],
    }


def test_receipt_hotspot_test_from_param_and_verifies(base_run):
    hset = load_hotspot_set()
    masks = _detected(hset)
    missed_id = hset.scenes[0].id
    del masks[missed_id]  # a miss blocks the PR
    gr = hotspot_set_gate(masks, hset.scenes)
    ht = {"passed": gr.passed, "reason": gr.reason, "detector": "SAM 3.1", **gr.detail}
    receipt = build_receipt(base_run, hotspot_test=ht)
    block = receipt["measurement"]["hotspot_test"]
    assert block["label"] == "SYNTHETIC"
    assert block["passed"] is False
    assert missed_id in block["missed"]
    assert block["est_wh_per_image"]["label"] == "ESTIMATE"
    assert block["detector"] == "SAM 3.1"
    ok, want, got = verify_hash(receipt)
    assert ok, (want, got)


def test_receipt_hotspot_test_from_events(base_run):
    hset = load_hotspot_set()
    gr = hotspot_set_gate(_detected(hset), hset.scenes)
    events = [{"type": "gate.result", "data": gr.event_data("t1")}]
    receipt = build_receipt(base_run, events=events)
    assert receipt["measurement"]["hotspot_test"]["passed"] is True
    assert receipt["measurement"]["hotspot_test"]["label"] == "SYNTHETIC"
    assert verify_hash(receipt)[0]


# --- runner -----------------------------------------------------------------

def test_run_hotspot_set_fake_passes():
    summary = run_hotspot_set(fake=True)
    assert summary["mode"] == "fake"
    assert summary["label"] == "SYNTHETIC"
    assert summary["detector"] == "SAM 3.1"
    assert summary["passed"]


def test_main_fake_passes_and_writes(tmp_path):
    rc = hotspot_main(["--fake", "--out", str(tmp_path), "--budget-s", "90"])
    assert rc == 0
    out = json.loads((tmp_path / "hotspot_set.json").read_text())
    assert out["mode"] == "fake" and out["passed"] and out["label"] == "SYNTHETIC"
