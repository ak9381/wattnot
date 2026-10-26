"""Tests for the manual rack overlay.

The overlay is a local JSON file of hand-drawn polygons
(``rack/masks.manual.json``) plus the rack photo those polygons sit on.
Nothing here calls a model or opens a network connection. Hardware is not
involved; the photo is a static file and the checks are pure.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from wattnot.sam import (
    MasksError,
    image_size,
    load_manual_overlay,
    load_masks,
    polygon_iou,
    validate_masks,
)
from wattnot.sam.segment import manual_overlay_path

REPO = Path(__file__).resolve().parents[1]

# Pixel space of rack/rack.jpg. Origin is the top-left, x right, y down.
PHOTO_SIZE = (640, 960)

RACK_POLYGON = [[160, 50], [470, 50], [470, 910], [160, 910]]
SLOT_POLYGONS = {
    0: [[190, 110], [430, 110], [430, 250], [190, 250]],
    1: [[190, 280], [430, 280], [430, 420], [190, 420]],
    2: [[190, 450], [430, 450], [430, 590], [190, 590]],
    3: [[190, 620], [430, 620], [430, 760], [190, 760]],
}
PDU_POLYGON = [[490, 110], [560, 110], [560, 820], [490, 820]]


def _write(tmp_path: Path, doc: dict, *, with_image: bool = True) -> Path:
    """Write a mask document next to a copy of the rack photo."""

    if with_image:
        name = doc.get("image", "rack.jpg")
        if isinstance(name, str) and "/" not in name and "\\" not in name:
            shutil.copyfile(REPO / "rack" / "rack.jpg", tmp_path / name)
    path = tmp_path / "masks.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _manual() -> dict:
    return load_manual_overlay()


# --- the committed manual overlay ------------------------------------------


def test_manual_overlay_lives_at_the_rack_path():
    path = manual_overlay_path()
    assert path == REPO / "rack" / "masks.manual.json"
    assert path.is_file()


def test_manual_overlay_validates():
    doc = _manual()
    assert doc["source"] == "manual"
    assert doc["image"] == "rack.jpg"
    assert doc["status"] == "ok"
    # The hand-drawn file is the reference, so its overlap with itself is 1.
    assert doc["iou_vs_manual"] == 1
    validate_masks(doc, image_path=REPO / "rack" / "rack.jpg")


def test_manual_overlay_labels_one_rack_four_slots_and_a_pdu():
    objects = {obj["id"]: obj for obj in _manual()["objects"]}
    assert list(objects) == ["rack-1", "slot-0", "slot-1", "slot-2", "slot-3", "pdu-1"]

    assert objects["rack-1"]["kind"] == "rack"
    assert objects["rack-1"]["polygon"] == RACK_POLYGON
    assert objects["rack-1"]["gpu_indices"] == [0, 1, 2, 3]

    for index, polygon in SLOT_POLYGONS.items():
        slot = objects[f"slot-{index}"]
        assert slot["kind"] == "slot"
        assert slot["polygon"] == polygon
        assert slot["gpu_indices"] == [index]

    assert objects["pdu-1"]["kind"] == "pdu"
    assert objects["pdu-1"]["polygon"] == PDU_POLYGON
    assert objects["pdu-1"]["gpu_indices"] == []


def test_manual_photo_is_a_jpeg_in_the_documented_pixel_space():
    photo = REPO / "rack" / "rack.jpg"
    assert image_size(photo) == PHOTO_SIZE
    # JPEG start-of-image marker. Catches a renamed non-JPEG.
    assert photo.read_bytes()[:2] == b"\xff\xd8"


def test_slot_polygons_sit_inside_the_rack():
    doc = _manual()
    rack = next(obj for obj in doc["objects"] if obj["kind"] == "rack")
    slots = [obj for obj in doc["objects"] if obj["kind"] == "slot"]
    assert slots
    for slot in slots:
        for point in slot["polygon"]:
            assert _point_in_convex(point, rack["polygon"])


def test_validator_accepts_a_sam_shaped_document_without_calling_out():
    """The schema allows a later ``sam3.1`` document. Building one in memory
    must not touch the network; this test never loads a client."""

    doc = _manual()
    doc["source"] = "sam3.1"
    doc["iou_vs_manual"] = 0.86
    doc["status"] = "ok"
    validate_masks(doc)


# --- loader rejects bad documents ------------------------------------------


def test_rejects_invalid_json(tmp_path: Path):
    path = tmp_path / "masks.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(MasksError, match="invalid JSON"):
        load_masks(path)


def test_rejects_non_finite_number(tmp_path: Path):
    doc = _manual()
    text = json.dumps(doc)
    needle = '"iou_vs_manual": ' + json.dumps(doc["iou_vs_manual"])
    assert needle in text
    path = tmp_path / "masks.json"
    path.write_text(text.replace(needle, '"iou_vs_manual": NaN', 1), encoding="utf-8")
    with pytest.raises(MasksError, match="finite"):
        load_masks(path)


def test_rejects_unknown_source():
    doc = _manual()
    doc["source"] = "hand"
    with pytest.raises(MasksError, match="source"):
        validate_masks(doc)


def test_rejects_extra_field():
    doc = _manual()
    doc["note"] = "nope"
    with pytest.raises(MasksError, match="note"):
        validate_masks(doc)


def test_rejects_manual_iou_other_than_one():
    doc = _manual()
    doc["iou_vs_manual"] = 0.5
    with pytest.raises(MasksError, match="iou_vs_manual"):
        validate_masks(doc)


def test_rejects_polygon_with_too_few_points():
    doc = _manual()
    doc["objects"][0]["polygon"] = [[0, 0], [1, 0]]
    with pytest.raises(MasksError, match="polygon"):
        validate_masks(doc)


def test_rejects_polygon_outside_the_photo(tmp_path: Path):
    doc = _manual()
    # A convex bar that runs past the right edge of the photo.
    doc["objects"][0]["polygon"] = [[0, 0], [9000, 0], [9000, 10], [0, 10]]
    path = _write(tmp_path, doc)
    with pytest.raises(MasksError, match="outside"):
        load_masks(path)


def test_rejects_non_convex_polygon():
    doc = _manual()
    # A bowtie. The two triangles cancel, and the winding flips.
    doc["objects"][0]["polygon"] = [[0, 0], [3, 1], [0, 1], [3, 0]]
    with pytest.raises(MasksError, match="convex"):
        validate_masks(doc)


def test_rejects_duplicate_slot_gpu(tmp_path: Path):
    doc = _manual()
    for obj in doc["objects"]:
        if obj["id"] == "slot-1":
            obj["gpu_indices"] = [0]
    path = _write(tmp_path, doc)
    with pytest.raises(MasksError, match="gpu 0"):
        load_masks(path)


def test_rejects_pdu_with_a_gpu():
    doc = _manual()
    for obj in doc["objects"]:
        if obj["kind"] == "pdu":
            obj["gpu_indices"] = [0]
    with pytest.raises(MasksError, match="pdu"):
        validate_masks(doc)


def test_rejects_slot_gpu_missing_from_its_rack():
    doc = _manual()
    for obj in doc["objects"]:
        if obj["kind"] == "rack":
            obj["gpu_indices"] = [0, 1, 2]
    with pytest.raises(MasksError, match="gpu 3"):
        validate_masks(doc)


def test_rejects_missing_image(tmp_path: Path):
    doc = _manual()
    path = _write(tmp_path, doc, with_image=False)
    with pytest.raises(MasksError, match="rack.jpg"):
        load_masks(path)


def test_rejects_image_name_with_a_directory():
    doc = _manual()
    doc["image"] = "sub/rack.jpg"
    with pytest.raises(MasksError, match="image"):
        validate_masks(doc)


def test_rejects_duplicate_ids():
    doc = _manual()
    doc["objects"][1]["id"] = doc["objects"][0]["id"]
    with pytest.raises(MasksError, match="duplicate id"):
        validate_masks(doc)


# --- polygon IoU on known shapes -------------------------------------------


def test_iou_of_identical_polygons_is_one():
    square = [[0, 0], [2, 0], [2, 2], [0, 2]]
    assert polygon_iou(square, square) == 1


def test_iou_of_disjoint_polygons_is_zero():
    left = [[0, 0], [1, 0], [1, 1], [0, 1]]
    right = [[2, 0], [3, 0], [3, 1], [2, 1]]
    assert polygon_iou(left, right) == 0


def test_iou_of_half_shifted_squares():
    # Each square has area 4. The overlap is a 1×2 rectangle (area 2).
    # Union is 6, so IoU is 1/3.
    left = [[0, 0], [2, 0], [2, 2], [0, 2]]
    right = [[1, 0], [3, 0], [3, 2], [1, 2]]
    assert polygon_iou(left, right) == pytest.approx(1 / 3)


def test_iou_when_one_polygon_contains_the_other():
    outer = [[0, 0], [4, 0], [4, 4], [0, 4]]
    inner = [[1, 1], [2, 1], [2, 2], [1, 2]]
    # Intersection 1, union 16, IoU 1/16.
    assert polygon_iou(outer, inner) == pytest.approx(1 / 16)


def test_iou_rejects_a_non_convex_polygon():
    bowtie = [[0, 0], [3, 1], [0, 1], [3, 0]]
    square = [[0, 0], [1, 0], [1, 1], [0, 1]]
    with pytest.raises(ValueError, match="convex"):
        polygon_iou(bowtie, square)


def _point_in_convex(point: list[float], polygon: list[list[int]]) -> bool:
    """Boundary counts as inside. Winding follows the signed area."""

    x, y = point
    sign = 0
    n = len(polygon)
    for i in range(n):
        ax, ay = polygon[i]
        bx, by = polygon[(i + 1) % n]
        cross = (bx - ax) * (y - ay) - (by - ay) * (x - ax)
        if cross == 0:
            continue
        s = 1 if cross > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            return False
    return True
