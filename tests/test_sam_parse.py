from __future__ import annotations

import json

from wattnot.sam import load_manual_overlay, validate_masks
from wattnot.sam.pick import pick_masks
from wattnot.sam.sam_parse import box_polygon, build_document, parse_boxes
from wattnot.sam.segment import repo_root

RACK = repo_root() / "rack"


def _line(polys, w=640, h=960):
    recs = []
    for i, poly in enumerate(polys):
        (x1, y1), (x2, y2) = poly[0], poly[2]
        recs.append(f"{i}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={w};h={h};c=0.9|>"
                    f"<|mask;x=0;y=0;data=4,4,~abc|>")
    return "<0f>" + ",".join(recs)


def _raw_close_to_manual(shift=4):
    manual = load_manual_overlay()
    by_kind: dict[str, list] = {}
    for obj in manual["objects"]:
        poly = [[x + shift, y + shift] for x, y in obj["polygon"]]
        by_kind.setdefault(obj["kind"], []).append(poly)
    return {k: _line(v) for k, v in by_kind.items()}


def test_parse_boxes_reads_fields_and_ignores_masks():
    boxes = parse_boxes("<0f>0<|box;x1=1;y1=2;x2=30;y2=40;w=640;h=960|><|mask;x=0;y=0;"
                        "data=3,3,~|x=|>,1<|box;x1=5;y1=6;x2=7;y2=8;w=640;h=960;c=0.5|>")
    assert len(boxes) == 2 and boxes[0]["x2"] == 30 and boxes[1]["c"] == 0.5
    assert box_polygon(boxes[0]) == [[1, 2], [30, 2], [30, 40], [1, 40]]
    assert parse_boxes("") == []


def test_good_sam_output_maps_gpus_and_passes():
    doc = build_document(_raw_close_to_manual(), load_manual_overlay())
    assert doc["status"] == "ok" and doc["iou_vs_manual"] >= 0.8
    validate_masks(doc, image_path=RACK / "rack.jpg")
    slots = {o["id"]: o["gpu_indices"] for o in doc["objects"] if o["kind"] == "slot"}
    assert slots == {"slot-0": [0], "slot-1": [1], "slot-2": [2], "slot-3": [3]}


def test_poor_overlap_is_cut():
    doc = build_document(_raw_close_to_manual(shift=60), load_manual_overlay())
    assert doc["status"] == "cut"


def test_recorded_empty_output_falls_back():
    raw = {k: (RACK / "sam-raw" / f"{k}.txt").read_text() for k in ("rack", "slot", "pdu")}
    doc = build_document(raw, load_manual_overlay())
    assert doc["status"] == "cut" and doc["objects"] == []
    picked, fallback = pick_masks()
    assert fallback and picked["source"] == "manual"


def test_spend_log_stays_inside_the_call_cap():
    spend = json.loads((RACK / "sam.spend.json").read_text())
    assert spend["model"] == "sam-3.1" and spend["calls"] <= 5
