"""The rehearsal's room scan payloads: shape, labels, order and power rule.

The run emits two `step.progress` events with `data.kind == "room_scan"`
(`phase` "before", then "after"; see `docs/SAM-ROLE.md`, "Events"). Each rack
carries `{id, polygon, temp_c, power_w, gpus, decision, cap_w, reason,
source}` with `decision` one of `derate`, `hold`, `ok`, `none` (and
`pending` while the run is still measuring the slot). Per-rack labels live
in the payload's `labels` dict, not on each rack: in rehearsal the slot's
power label is MOCK (MEASURED on a live run).

No network, no GPU: `--mode rehearsal` runs on the fake meter.
"""

from __future__ import annotations

import json

import pytest

from wattnot.dash.pipeline import validate_room_scan
from wattnot.events import Emitter
from wattnot.proof import orchestrate

pytest.importorskip("wattnot.sam.room")

# Runs a whole rehearsal, so it is slow; it runs in the full suite at integration.
pytestmark = pytest.mark.slow

LABELS = {"MEASURED", "ESTIMATE", "SIMULATED", "REPLAY", "ADVICE", "MOCK"}
DECISIONS = {"derate", "hold", "ok", "none", "pending"}
SLOT_SOURCE = "h100-run1"


@pytest.fixture(scope="module")
def rehearsal(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("roomscan")
    live = tmp / "live" / "run.events.jsonl"
    argv = ["orchestrate", "--mode", "rehearsal", "--out", str(tmp / "o"),
            "--live-file", str(live), "--load-s", "1", "--reps", "2", "--repeat", "1",
            "--concurrency", "8", "--warmup", "1", "--settle-s", "0.05",
            "--adaptive-budget-s", "30"]
    import sys as _sys

    old = _sys.argv  # main() reads sys.argv; other tests use monkeypatch the same way
    _sys.argv = argv
    try:
        orchestrate.main()
    finally:
        _sys.argv = old
    events = [json.loads(x) for x in live.read_text().splitlines()]
    scans = [e for e in events
             if e["type"] == "step.progress" and e["data"].get("kind") == "room_scan"]
    before_file = next((tmp / "o").rglob("room_scan_before.json"), None)
    return {"events": events, "scans": scans, "root": tmp, "before_file": before_file}


def test_two_phases_before_then_after(rehearsal):
    assert [s["data"]["phase"] for s in rehearsal["scans"]] == ["before", "after"]


def test_scan_events_pass_the_frozen_schema(rehearsal, tmp_path):
    em = Emitter("run_20260101_000000_abcdef", root=tmp_path / "v")
    for s in rehearsal["scans"]:
        em.emit(s["type"], s["data"], step_id=s.get("step_id"),
                parent_step_id=s.get("parent_step_id"))


def test_full_scan_is_wattnot_room_1(rehearsal):
    assert rehearsal["before_file"] is not None
    assert json.loads(rehearsal["before_file"].read_text())["format"] == "wattnot.room/1"


def test_rack_shape_and_labels(rehearsal):
    for s in rehearsal["scans"]:
        d = s["data"]
        assert d["room_id"] and d["sam"]["model"] == "sam-3.1"
        assert len(d["racks"]) >= 1
        for r in d["racks"]:
            assert isinstance(r["id"], str) and r["id"]
            assert isinstance(r["polygon"], list) and len(r["polygon"]) >= 3
            assert all(len(p) == 2 for p in r["polygon"])
            assert r["temp_c"] is None or isinstance(r["temp_c"], (int, float))
            assert r["power_w"] is None or isinstance(r["power_w"], (int, float))
            assert r["decision"] in DECISIONS, r
        for k, v in d["labels"].items():
            if k.endswith("_note"):  # a reading note, not a label
                continue
            assert v in LABELS, (k, v)


def test_one_h100_rack_and_mock_slot_power(rehearsal):
    for s in rehearsal["scans"]:
        slots = [r for r in s["data"]["racks"] if r.get("source") == SLOT_SOURCE]
        assert len(slots) == 1
        assert s["data"]["labels"]["h100_slot_power_w"] == "MOCK"


def test_after_never_raises_rack_power(rehearsal):
    before = {r["id"]: r for r in rehearsal["scans"][0]["data"]["racks"]}
    after = {r["id"]: r for r in rehearsal["scans"][1]["data"]["racks"]}
    assert set(after) == set(before)
    for rid, b in before.items():
        a = after[rid]
        if b["power_w"] is not None and a["power_w"] is not None:
            assert a["power_w"] <= b["power_w"], rid


@pytest.mark.parametrize("patch,why", [
    ({"schema": "wattnot.room/9"}, "wrong schema"),
    ({"racks": []}, "no racks"),
    ({"racks": [{"id": "R-01", "polygon": [[0, 0], [1, 1]],
                                "decision": "act"}]}, "short polygon"),
    ({"racks": [{"id": "R-01", "polygon": [[0, 0], [1, 0], [1, 1]],
                                "decision": "cap"}]}, "bad decision"),
    ({"racks": [{"id": "R-01", "polygon": [[0, 0], [1, 0], [1, 1]],
                                "decision": "act", "power_label": "GUESSED"}]},
     "bad label"),
])
def test_validate_room_scan_rejects_bad_payloads(patch, why):
    good = {"schema": "wattnot.room/1", "size": [400, 240],
            "racks": [{"id": "R-01", "polygon": [[0, 0], [1, 0], [1, 1]],
                       "temp_c": 30.0, "power_w": 400.0, "decision": "act"}]}
    bad = {**good, **patch}
    if "racks" in patch and isinstance(patch["racks"], list) and patch["racks"]:
        bad["racks"] = [{**good["racks"][0], **patch["racks"][0]}]
    with pytest.raises(ValueError):
        validate_room_scan(bad)


def test_validate_room_scan_accepts_a_good_payload():
    scan = validate_room_scan(
        {"schema": "wattnot.room/1", "size": [400, 240],
         "racks": [{"id": "R-01", "polygon": [[0, 0], [1, 0], [1, 1]],
                    "temp_c": 30.0, "temp_label": "SIMULATED",
                    "power_w": 400.0, "power_label": "MEASURED",
                    "gpu": True, "decision": "act", "reason": "hot"}]})
    assert scan["racks"][0]["id"] == "R-01"
