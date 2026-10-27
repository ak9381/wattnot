"""Room scan: SAM rack masks + thermal frame + power cross-check -> decision per rack.

Replays saved SAM output (rack/room/arsat/sam-raw). No network, no key, no spend.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PIL")
pytest.importorskip("meta_sam_parser")

from wattnot.sam import ablation as ab  # noqa: E402
from wattnot.sam import physical as ph  # noqa: E402
from wattnot.sam import room  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "rack/room/scenario.json").read_text())
TH = {**CFG["thresholds"], "r_air": CFG["r_air_c_per_w"]}


@pytest.fixture(scope="module")
def scan(tmp_path_factory):
    return room.build_scan("arsat", out=tmp_path_factory.mktemp("room"))


def test_sam_finds_eleven_racks_left_to_right():
    raw = (ROOT / "rack/room/arsat/sam-raw/photo.server-rack.txt").read_text()
    ms = room.masks_from_raw(raw)
    assert len(ms) == raw.count("<|box") == 11
    assert [m.id for m in ms] == [f"R{i:02d}" for i in range(1, 12)]
    xs = [m.box[0] + m.box[2] for m in ms]
    assert xs == sorted(xs)
    assert all(m.full().sum() > 3000 for m in ms)


def test_palette_round_trips_within_a_degree():
    t = np.linspace(22, 48, 200)
    back = room.from_rgb(room.to_rgb(t, 20, 50), 20, 50)
    assert np.abs(back - t).max() < 1.2


def test_decide_cases():
    hot_hi = room.decide(47.0, 480, TH)
    assert hot_hi["action"] == "derate" and 250 <= hot_hi["cap_w"] < 480
    hot_lo = room.decide(46.0, 270, TH)
    assert hot_lo["action"] == "hold" and "cap_w" not in hot_lo
    assert room.decide(40.0, 480, TH)["action"] == "ok"
    assert room.decide(None, 480, TH)["action"] == "none"


def test_cap_brings_the_rack_under_the_hot_line(scan):
    hot_c = scan["thermal"]["hot_c"]
    for r in scan["racks"]:
        if r["decision"] == "derate":
            assert r["before"]["temp_c"] >= hot_c > r["after"]["temp_c"]
            assert r["after"]["power_w"] == r["cap_w"] < r["before"]["power_w"]


def test_hot_high_power_is_capped_and_hot_low_power_is_held(scan):
    by = {r["id"]: r for r in scan["racks"]}
    assert by["R01"]["decision"] == by["R03"]["decision"] == by["R10"]["decision"] == "derate"
    assert by["R04"]["decision"] == "hold" and by["R04"]["cap_w"] is None
    assert by["R04"]["after"] == by["R04"]["before"]  # a cap would not have fixed it
    assert scan["hotspots"][0] in ("R01", "R10")  # ranked hottest first
    assert scan["after"]["max_c"] < scan["before"]["max_c"]
    saved = scan["before"]["room_power_w"] - scan["after"]["room_power_w"]
    assert scan["after"]["saved_w"] == saved > 0


def test_planner_and_gate_follow_the_thermal_check(scan):
    fleet = scan["_fleet"]
    by = {r["id"]: r for r in scan["racks"]}
    g_hot, g_fault, g_ok = (by[k]["gpus"][0]["id"] for k in ("R01", "R04", "R02"))
    lim = fleet.limit_for(g_hot)
    assert lim.eligible and lim.ceiling_w == by["R01"]["cap_w"] < 600
    from wattnot.approvals.proposers import physical_check
    assert not physical_check(fleet, g_hot, lim.ceiling_w + 50)["passed"]
    assert physical_check(fleet, g_hot, lim.ceiling_w)["passed"]
    fault = fleet.limit_for(g_fault)
    assert not fault.eligible and "cooling" in fault.reason
    with pytest.raises(ph.NotVerified):
        fleet.require(g_fault)
    assert ab._planner(fleet, g_fault)["refused"]
    run = ab._planner(fleet, g_hot)
    assert run["cap_w"] <= lim.ceiling_w
    assert fleet.limit_for(g_ok).ceiling_w == 600  # untouched rack keeps the budget ceiling


def test_recommend_items_carry_labels(scan):
    rack = scan["_fleet"].rack_of(scan["racks"][0]["gpus"][0]["id"])
    items = rack.recommend_items(0)
    assert {i.label for i in items} <= {"ESTIMATE", "ADVICE", "MEASURED"}
    assert any(i.key == "thermal_action" for i in items)


def test_sam_off_cannot_tell_racks_apart(scan):
    off = scan["sam_off"]
    assert off["racks_cut_needlessly"] > 0 and off["cooling_faults_missed"] == 1
    assert off["watts_lost"] > off["watts_lost_with_sam"]


def test_scan_json_schema_and_labels(scan, tmp_path):
    pub = room.public(scan)
    json.dumps(pub)  # serialisable
    for k in ("room_id", "image", "thermal_image", "labels", "racks", "before", "after"):
        assert k in pub
    for r in pub["racks"]:
        for k in ("id", "polygon", "temp_c", "power_w", "gpus", "decision", "reason"):
            assert k in r
        assert len(r["polygon"]) >= 6 and r["path"].startswith("M")
        assert len(r["gpus"]) == CFG["gpus_per_rack"]
    lab = pub["labels"]
    assert lab["rack_count"] == "MEASURED" and lab["gpu_power_w"] == "REPLAY"
    assert lab["thermal_frame"] == lab["rack_temp_c"] == "SIMULATED"
    assert lab["rack_to_gpu_map"] == lab["gpu_temp_c"] == "SIMULATED"
    assert pub["sam"]["racks_found"] == 11


def test_files_written(tmp_path):
    room.build_scan("arsat", out=tmp_path)
    for f in ("scan.json", "photo.jpg", "thermal.jpg", "thermal_after.jpg"):
        assert (tmp_path / f).stat().st_size > 1000
    assert json.loads((tmp_path / "scan.json").read_text())["format"] == "wattnot.room/1"


def test_real_ir_still_goes_through_the_same_path(tmp_path):
    ir = ROOT / "rack/room/irpair/thermal.jpg"
    s = room.build_scan("arsat", thermal=ir, value_range=(20, 41.5), out=tmp_path)
    assert s["labels"]["rack_temp_c"] == "ESTIMATE" and s["thermal"]["mode"] == "ir-still"
    assert s["thermal_after_image"] is None
    assert all(r["temp_c"] is None or 20 <= r["temp_c"] <= 41.6 for r in s["racks"])


def test_ir_read_without_masks():
    r = room.read_ir_still(ROOT / "rack/room/irpair/thermal.jpg", 20, 41.5)
    assert r["label"] == "ESTIMATE" and len(r["bands_top_to_bottom"]) == 8
    assert 40 <= r["max_c"] <= 41.5


def test_trace_is_the_real_replay():
    t = room.load_trace()
    assert len(t["power_w"]) == 420 and 400 < t["power_w"].min() < t["power_w"].max() < 520
    p0, p1 = (room.gpu_power(t, 0, g, 1.0) for g in (0, 1))
    assert not np.array_equal(p0, p1) and np.isclose(p0.mean(), p1.mean())


def test_real_h100_rack_is_marked_and_uses_its_own_log(scan):
    by = {r["id"]: r for r in scan["racks"]}
    real = [r for r in scan["racks"] if r["source"] == "h100-run1"]
    assert [r["id"] for r in real] == ["R03"] == [scan["real_rack"]]
    assert sum(g["measured"] for r in scan["racks"] for g in r["gpus"]) == 1
    assert by["R03"]["gpus"][0]["measured"] and by["R01"]["source"] == "example"
    t = room.load_trace()
    assert by["R03"]["gpus"][0]["power_w"] == round(float(t["power_w"][scan["thermal"]["t_index"]]))
    assert scan["labels"]["h100_gpu_power_w"] == "MEASURED"


def test_targets_order_for_muse_spark(scan):
    t = room.targets(scan)
    assert len(t) == 11 and t[0]["action"] == "derate"
    acts = [x["action"] for x in t]
    assert acts == sorted(acts, key=["derate", "hold", "ok", "none"].index)
    assert t[0]["gpus"] and all("reason" in x for x in t)
    assert "image" not in json.dumps(t)


def test_events_are_valid_and_carry_before_and_after(scan, tmp_path):
    from wattnot.events import Emitter
    from wattnot.events.emitter import new_run_id

    with Emitter(new_run_id(), root=tmp_path) as em:  # validates against run-event.schema.json
        evs = room.emit_events(scan, em)
    assert [e["type"] for e in evs] == ["step.started", "step.progress", "step.progress",
                                        "step.finished"]
    before, after = evs[1]["data"], evs[2]["data"]
    assert before["kind"] == after["kind"] == "room_scan"
    assert (before["phase"], after["phase"]) == ("before", "after")
    assert before["targets"] and after["targets"] is None
    b = {r["id"]: r for r in before["racks"]}
    a = {r["id"]: r for r in after["racks"]}
    assert a["R01"]["temp_c"] < b["R01"]["temp_c"] and a["R04"]["temp_c"] == b["R04"]["temp_c"]
    for k in ("room_id", "image", "thermal_image", "labels", "racks"):
        assert k in before
    for r in before["racks"]:
        for k in ("id", "polygon", "temp_c", "power_w", "gpus", "decision", "reason"):
            assert k in r


def test_thermal_goes_into_the_summary_muse_spark_sees(scan):
    gpu = next(r for r in scan["racks"] if r["id"] == "R01")["gpus"][0]["id"]
    summ = scan["_fleet"].rack_of(gpu).summary()
    assert summ["thermal"]["action"] == "derate" and "image" not in json.dumps(summ)
