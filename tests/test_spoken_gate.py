"""Watts per spoken answer (the spoken-answer gate): asset loading, matchers,
the gate, its energy triplet, its place in the keep/reject loop, the receipt
block, and the --fake runner."""

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
from wattnot.spokenanswer import (
    grade_answers,
    ground_truth,
    load_spoken_set,
    spoken_answer_gate,
    spoken_energy,
)
from wattnot.spokenanswer.client import OfflineAnswerer, answer_spoken_set
from wattnot.spokenanswer.run import main as run_main

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "receipts"
RUN = "run_20261030_000000_abcdef"
MODEL = "meta-models/Muse-Glimmer-30B"


# --- assets -----------------------------------------------------------------

def test_assets_load_and_are_synthetic():
    spoken = load_spoken_set(require_files=True)
    assert len(spoken) >= 20
    assert spoken.label == "SYNTHETIC"
    assert all(it.label == "SYNTHETIC" for it in spoken)
    # Every committed placeholder recording exists next to the manifest.
    assert all(it.path(spoken.base_dir).exists() for it in spoken)
    kinds = {it.kind for it in spoken}
    noises = {it.noise for it in spoken}
    assert {"text", "number"} <= kinds
    assert {"clean", "datacenter"} == noises
    # Both a clean and a data-center-noise take exist for the same questions.
    assert any(it.noise == "datacenter" for it in spoken)


def test_rejects_non_synthetic_manifest(tmp_path):
    (tmp_path / "answers.json").write_text(json.dumps({"label": "REAL", "items": []}))
    with pytest.raises(ValueError):
        load_spoken_set(tmp_path)


# --- matchers / gate --------------------------------------------------------

def test_gate_passes_on_ground_truth():
    spoken = load_spoken_set()
    r = spoken_answer_gate(ground_truth(spoken.items), spoken.items)
    assert r.passed
    assert r.gate == "spoken"
    assert r.detail["missed"] == []
    assert r.detail["passed_count"] == r.detail["checked"] == len(spoken)
    assert r.detail["blocks_musecode_pr"] is False
    assert r.detail["label"] == "SYNTHETIC"


def test_gate_tolerates_phrasing_but_catches_a_miss():
    spoken = load_spoken_set()
    ans = dict(ground_truth(spoken.items))
    # Forgiving matchers: the model may answer in a short phrase.
    ans["q-light-clean"] = "it looks green to me"
    ans["q-cap-clean"] = "The power cap is 350 watts."
    ans["q-gpus-clean"] = "there are 8 GPUs"
    r = spoken_answer_gate(ans, spoken.items)
    assert r.passed, r.reason

    # A single wrong spoken answer rejects the candidate, just like the eye test.
    ans["q-cap-datacenter"] = "about 3 fifty"   # no numeric 350 -> a miss
    bad = spoken_answer_gate(ans, spoken.items)
    assert not bad.passed
    assert "q-cap-datacenter" in bad.detail["missed"]
    # It was a data-center-noise take, so it is called out as such.
    assert "q-cap-datacenter" in bad.detail["missed_noisy"]
    assert bad.detail["blocks_musecode_pr"] is True


def test_missing_answer_counts_as_miss():
    spoken = load_spoken_set()
    ans = dict(ground_truth(spoken.items))
    del ans["q-bay-clean"]
    r = spoken_answer_gate(ans, spoken.items)
    assert not r.passed and "q-bay-clean" in r.detail["missed"]


def test_grade_answers_trims_long_output():
    spoken = load_spoken_set()
    rows = grade_answers({spoken.items[0].id: "x" * 500}, spoken.items[:1])
    assert len(rows[0]["got"]) <= 64
    assert rows[0]["noise"] in ("clean", "datacenter")


def test_empty_answers_fail_with_no_crash():
    spoken = load_spoken_set()
    r = spoken_answer_gate({}, spoken.items)
    assert not r.passed and len(r.detail["missed"]) == len(spoken)


# --- energy triplet ---------------------------------------------------------

def test_spoken_energy_three_numbers():
    e = spoken_energy(wh_per_answer=0.35, tokens_per_answer=6.0, pass_rate=0.9,
                      label="MEASURED", j_per_correct_answer=42.0)
    assert e["label"] == "MEASURED"
    assert e["j_per_spoken_answer"] == pytest.approx(1260.0)      # 0.35 Wh x 3600
    assert e["j_per_correct_spoken"] == pytest.approx(1400.0)     # 1260 / 0.9
    assert e["j_per_token"] == pytest.approx(210.0)               # 1260 / 6
    assert e["j_per_correct_answer"] == pytest.approx(42.0)


def test_spoken_energy_guards_zero():
    e = spoken_energy(wh_per_answer=0.1, tokens_per_answer=0.0, pass_rate=0.0)
    assert e["j_per_correct_spoken"] is None
    assert e["j_per_token"] is None


# --- keep/reject loop -------------------------------------------------------

def _trial(tid: str) -> TrialResult:
    return TrialResult(tid, TrialParams(), (), 20, 0.01, 1.0, 0.5, 0.9, 1.0)


def _items() -> list[WorkloadItem]:
    return [WorkloadItem("a", "x", 8, {"checks": [{"type": "exact", "value": "42"}]})]


def test_spoken_failure_rolls_back_to_last_safe_config(tmp_path):
    spoken = load_spoken_set()
    ans = dict(ground_truth(spoken.items))
    ans["q-meter-datacenter"] = "275.5 watts... maybe 999"  # mishears the meter reading
    sg = spoken_answer_gate(ans, spoken.items)
    assert not sg.passed
    calls = []
    with Emitter(RUN, root=tmp_path) as em:
        d = decide(_trial("b"), _trial("t1"), {"a": "42"}, {"a": "42"}, _items(),
                   spoken=sg, emit=em)
        ok = guarded_apply(d, apply=lambda: calls.append("apply"),
                           revert=lambda: calls.append("revert"),
                           change={"power_cap_w": 300}, emit=em)
    assert not ok and calls == ["revert"]
    assert "spoken" in d.failed
    ev = [json.loads(x) for x in (tmp_path / "runs" / RUN / "events.jsonl").read_text().splitlines()]
    gates = [e["data"]["gate"] for e in ev if e["type"] == "gate.result"]
    assert "spoken" in gates
    assert ev[-1]["type"] == "action.rolled_back"
    assert "spoken" in ev[-1]["data"]["failed_gates"]


def test_spoken_pass_keeps_change(tmp_path):
    spoken = load_spoken_set()
    sg = spoken_answer_gate(ground_truth(spoken.items), spoken.items)
    calls = []
    with Emitter(RUN, root=tmp_path) as em:
        d = decide(_trial("b"), _trial("t1"), {"a": "42"}, {"a": "42"}, _items(),
                   spoken=sg, emit=em)
        guarded_apply(d, apply=lambda: calls.append("apply"),
                      revert=lambda: calls.append("revert"), change={}, emit=em)
    assert d.passed and calls == ["apply"]


# --- receipt ----------------------------------------------------------------

@pytest.fixture
def base_run():
    valid = json.loads((FIXTURES / "valid.json").read_text())
    return {
        "run_id": "run_20261030_150000_spktest",
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


def test_receipt_spoken_test_from_param_and_verifies(base_run):
    spoken = load_spoken_set()
    ans = dict(ground_truth(spoken.items))
    ans["q-rack-datacenter"] = "R13"  # a wrong spoken answer blocks the PR
    energy = spoken_energy(wh_per_answer=0.35, tokens_per_answer=6.0,
                           pass_rate=(len(spoken) - 1) / len(spoken),
                           label="MEASURED", j_per_correct_answer=42.0)
    sg = spoken_answer_gate(ans, spoken.items, energy=energy)
    st = {"passed": sg.passed, "reason": sg.reason, "model": MODEL,
          "transcriber": spoken.transcriber, **sg.detail}
    receipt = build_receipt(base_run, spoken_test=st)
    block = receipt["measurement"]["spoken_test"]
    assert block["label"] == "SYNTHETIC"
    assert block["passed"] is False
    assert "q-rack-datacenter" in block["missed"]
    assert block["model"] == MODEL
    assert block["transcriber"] == "muse-voice-transcribe-1.0"
    assert block["energy"]["label"] == "MEASURED"
    assert block["energy"]["j_per_correct_spoken"] is not None
    assert block["energy"]["j_per_token"] == pytest.approx(210.0)
    # No reference question text ever reaches the public receipt.
    assert all("question" not in row for row in block["items"])
    ok, want, got = verify_hash(receipt)
    assert ok, (want, got)


def test_receipt_spoken_test_from_events(base_run):
    spoken = load_spoken_set()
    sg = spoken_answer_gate(ground_truth(spoken.items), spoken.items)
    events = [{"type": "gate.result", "data": sg.event_data("t1")}]
    receipt = build_receipt(base_run, events=events)
    assert receipt["measurement"]["spoken_test"]["passed"] is True
    assert receipt["measurement"]["spoken_test"]["checked"] == len(spoken)
    assert verify_hash(receipt)[0]


# --- runner -----------------------------------------------------------------

def test_offline_answerer_and_budget():
    spoken = load_spoken_set()
    answers, meta = answer_spoken_set(spoken, OfflineAnswerer(spoken))
    assert meta["answered"] == len(spoken) and not meta["budget_hit"]
    assert spoken_answer_gate(answers, spoken.items).passed

    # A zero budget answers nothing, so the gate fails (every question a miss).
    ans2, meta2 = answer_spoken_set(spoken, OfflineAnswerer(spoken), budget_s=0.0)
    assert meta2["budget_hit"] and meta2["answered"] == 0
    assert not spoken_answer_gate(ans2, spoken.items).passed


def test_run_main_fake_passes_and_writes(tmp_path, capsys):
    rc = run_main(["--fake", "--out", str(tmp_path), "--budget-s", "90"])
    assert rc == 0
    out = json.loads((tmp_path / "spoken_answer.json").read_text())
    assert out["mode"] == "fake" and out["passed"] and out["label"] == "SYNTHETIC"
    # The --fake runner has no meter, so the joules triplet is reported as MOCK.
    assert out["detail"]["energy"]["label"] == "MOCK"
    assert out["detail"]["energy"]["j_per_correct_spoken"] is not None
    assert out["detail"]["energy"]["j_per_token"] is not None
