"""Vision quality gate (the eye test): asset loading, matchers, the gate, its
place in the keep/reject loop, the receipt block, and the --fake runner."""

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
from wattnot.vision import grade_answers, load_eye_test, vision_gate
from wattnot.vision.assets import ground_truth
from wattnot.vision.client import OfflineAnswerer, answer_eye_test
from wattnot.vision.run import main as run_main

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "receipts"
RUN = "run_20261030_000000_abcdef"


# --- assets -----------------------------------------------------------------

def test_assets_load_and_are_synthetic():
    eye = load_eye_test(require_files=True)
    assert len(eye) >= 4
    assert eye.label == "SYNTHETIC"
    assert all(it.label == "SYNTHETIC" for it in eye)
    # Every committed placeholder image exists next to the manifest.
    assert all(it.path(eye.base_dir).exists() for it in eye)
    kinds = {it.kind for it in eye}
    assert {"text", "number", "count", "color"} <= kinds


def test_rejects_non_synthetic_manifest(tmp_path):
    (tmp_path / "answers.json").write_text(json.dumps(
        {"label": "REAL", "items": []}))
    with pytest.raises(ValueError):
        load_eye_test(tmp_path)


# --- matchers / gate --------------------------------------------------------

def test_gate_passes_on_ground_truth():
    eye = load_eye_test()
    r = vision_gate(ground_truth(eye.items), eye.items, wh_per_image=eye.wh_per_image)
    assert r.passed
    assert r.detail["missed"] == []
    assert r.detail["passed_count"] == r.detail["checked"]
    assert r.detail["blocks_musecode_pr"] is False
    # Every image carries an ESTIMATE energy stamp.
    assert all(row["energy_wh"]["label"] == "ESTIMATE" for row in r.detail["images"])


def test_gate_tolerates_phrasing_but_catches_a_miss():
    eye = load_eye_test()
    ans = dict(ground_truth(eye.items))
    # Forgiving matchers: the model may answer in a short phrase.
    ans["invoice-total"] = "The total is $1,284.50"
    ans["count-cubes"] = "I count 7 cubes"
    ans["color-chart-top"] = "it looks magenta to me"
    r = vision_gate(ans, eye.items)
    assert r.passed, r.reason

    # A single misread rejects the candidate.
    ans["plate-bus789"] = "BUS-788"
    bad = vision_gate(ans, eye.items)
    assert not bad.passed
    assert "plate-bus789" in bad.detail["missed"]
    assert bad.detail["blocks_musecode_pr"] is True
    assert bad.gate == "vision"


def test_missing_answer_counts_as_miss():
    eye = load_eye_test()
    ans = dict(ground_truth(eye.items))
    del ans["gauge-reading"]
    r = vision_gate(ans, eye.items)
    assert not r.passed and "gauge-reading" in r.detail["missed"]


def test_grade_answers_trims_long_output():
    eye = load_eye_test()
    rows = grade_answers({eye.items[0].id: "x" * 500}, eye.items[:1])
    assert len(rows[0]["got"]) <= 64


# --- keep/reject loop -------------------------------------------------------

def _trial(tid: str) -> TrialResult:
    return TrialResult(tid, TrialParams(), (), 20, 0.01, 1.0, 0.5, 0.9, 1.0)


def _items() -> list[WorkloadItem]:
    return [WorkloadItem("a", "x", 8, {"checks": [{"type": "exact", "value": "42"}]})]


def test_vision_failure_rolls_back_to_last_safe_config(tmp_path):
    eye = load_eye_test()
    ans = dict(ground_truth(eye.items))
    ans["sign-exit"] = "EXIT 99Z"  # a misread
    vg = vision_gate(ans, eye.items)
    calls = []
    with Emitter(RUN, root=tmp_path) as em:
        d = decide(_trial("b"), _trial("t1"), {"a": "42"}, {"a": "42"}, _items(),
                   vision=vg, emit=em)
        ok = guarded_apply(d, apply=lambda: calls.append("apply"),
                           revert=lambda: calls.append("revert"),
                           change={"power_cap_w": 300}, emit=em)
    assert not ok and calls == ["revert"]
    assert "vision" in d.failed
    ev = [json.loads(x) for x in (tmp_path / "runs" / RUN / "events.jsonl").read_text().splitlines()]
    gates = [e["data"]["gate"] for e in ev if e["type"] == "gate.result"]
    assert "vision" in gates
    assert ev[-1]["type"] == "action.rolled_back"
    assert "vision" in ev[-1]["data"]["failed_gates"]


def test_vision_pass_keeps_change(tmp_path):
    eye = load_eye_test()
    vg = vision_gate(ground_truth(eye.items), eye.items)
    calls = []
    with Emitter(RUN, root=tmp_path) as em:
        d = decide(_trial("b"), _trial("t1"), {"a": "42"}, {"a": "42"}, _items(),
                   vision=vg, emit=em)
        guarded_apply(d, apply=lambda: calls.append("apply"),
                      revert=lambda: calls.append("revert"), change={}, emit=em)
    assert d.passed and calls == ["apply"]


# --- receipt ----------------------------------------------------------------

@pytest.fixture
def base_run():
    valid = json.loads((FIXTURES / "valid.json").read_text())
    return {
        "run_id": "run_20261030_150000_vistest",
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


def test_receipt_eye_test_from_param_and_verifies(base_run):
    eye = load_eye_test()
    ans = dict(ground_truth(eye.items))
    ans["plate-van204"] = "VAN-205"  # a misread blocks the PR
    vg = vision_gate(ans, eye.items)
    et = {"passed": vg.passed, "reason": vg.reason, "model": "meta-models/Muse-Glimmer-30B",
          **vg.detail}
    receipt = build_receipt(base_run, eye_test=et)
    block = receipt["measurement"]["eye_test"]
    assert block["label"] == "SYNTHETIC"
    assert block["passed"] is False
    assert "plate-van204" in block["missed"]
    assert block["est_wh_per_image"]["label"] == "ESTIMATE"
    assert block["model"] == "meta-models/Muse-Glimmer-30B"
    ok, want, got = verify_hash(receipt)
    assert ok, (want, got)


def test_receipt_eye_test_from_events(base_run):
    eye = load_eye_test()
    vg = vision_gate(ground_truth(eye.items), eye.items)
    events = [{"type": "gate.result", "data": vg.event_data("t1")}]
    receipt = build_receipt(base_run, events=events)
    assert receipt["measurement"]["eye_test"]["passed"] is True
    assert verify_hash(receipt)[0]


# --- runner -----------------------------------------------------------------

def test_offline_answerer_and_budget():
    eye = load_eye_test()
    answers, meta = answer_eye_test(eye, OfflineAnswerer(eye))
    assert meta["answered"] == len(eye) and not meta["budget_hit"]
    assert vision_gate(answers, eye.items).passed

    # A zero budget answers nothing, so the gate fails (every image a miss).
    ans2, meta2 = answer_eye_test(eye, OfflineAnswerer(eye), budget_s=0.0)
    assert meta2["budget_hit"] and meta2["answered"] == 0
    assert not vision_gate(ans2, eye.items).passed


def test_run_main_fake_passes_and_writes(tmp_path, capsys):
    rc = run_main(["--fake", "--out", str(tmp_path), "--budget-s", "90"])
    assert rc == 0
    out = json.loads((tmp_path / "eye_test.json").read_text())
    assert out["mode"] == "fake" and out["passed"] and out["label"] == "SYNTHETIC"
