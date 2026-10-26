"""Turn SAM 3.1 output into a mask document in the manual overlay's shape.

SAM returns one text line per image: `<0f>` then records like
`0<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..|><|mask;...|>`, comma separated.
We keep the boxes (axis-aligned rectangles are convex polygons, which is
what the overlay format and IoU code expect) and drop the mask payload.

GPU indices come from the manual overlay: each SAM object takes the GPU
list of the manual object of the same kind it overlaps most. The document
is `ok` only if every rack, slot and PDU in the manual overlay is matched
with IoU >= 0.8; otherwise it is `cut` and the rack view falls back.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from wattnot.sam.compare import polygon_iou

IOU_MIN = 0.8
_BOX_RE = re.compile(r"<\|box;([^|]*)\|>")
_REC_RE = re.compile(r"(?:^|,|<\d+f>)(\d+)<\|box;([^|]*)\|>")


def parse_boxes(text: str) -> list[dict[str, float]]:
    """Every box in a SAM output line, as floats keyed x1, y1, x2, y2, w, h (and c)."""

    boxes = []
    for match in _BOX_RE.finditer(text):
        fields = dict(part.split("=", 1) for part in match.group(1).split(";") if "=" in part)
        boxes.append({k: float(v) for k, v in fields.items()})
    return boxes


def parse_by_object(text: str) -> dict[int, str]:
    """Split one output line into per-object records, keyed by object id.

    Used for box and point prompts, where we pick the ids ourselves.
    """

    out: dict[int, str] = {}
    for match in _REC_RE.finditer(text):
        out[int(match.group(1))] = f"<|box;{match.group(2)}|>"
    return out


def box_polygon(box: Mapping[str, float]) -> list[list[float]]:
    x1, y1, x2, y2 = box["x1"], box["y1"], box["x2"], box["y2"]
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _iou(a: Sequence, b: Sequence) -> float:
    try:
        return polygon_iou(a, b)
    except ValueError:
        return 0.0


def _clip_box(inner: Sequence, outer: Sequence) -> list[list[float]]:
    """Clip one axis-aligned box polygon to another (both from box_polygon)."""

    x1 = max(inner[0][0], outer[0][0])
    y1 = max(inner[0][1], outer[0][1])
    x2 = min(inner[2][0], outer[2][0])
    y2 = min(inner[2][1], outer[2][1])
    if x2 <= x1 or y2 <= y1:
        return [list(p) for p in inner]
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def build_document(raw_by_kind: Mapping[str, str], manual: Mapping[str, Any]) -> dict[str, Any]:
    """Build a `source: sam3.1` document from raw SAM output per kind.

    A slot box that pokes a few pixels past its rack box is clipped to the
    rack, so the overlay's slot-inside-rack check holds. Scores are taken
    after the clip.
    """

    rack_boxes: dict[int, list[list[float]]] = {}
    for ref in manual["objects"]:
        if ref["kind"] != "rack":
            continue
        cands = [box_polygon(b) for b in parse_boxes(raw_by_kind.get("rack", ""))]
        if cands:
            best = max(cands, key=lambda poly: _iou(poly, ref["polygon"]))
            for gpu in ref["gpu_indices"]:
                rack_boxes[gpu] = best

    objects: list[dict[str, Any]] = []
    scores: list[float] = []
    for ref in manual["objects"]:
        candidates = [box_polygon(b) for b in parse_boxes(raw_by_kind.get(ref["kind"], ""))]
        if ref["kind"] == "slot" and ref["gpu_indices"][0] in rack_boxes:
            outer = rack_boxes[ref["gpu_indices"][0]]
            candidates = [_clip_box(poly, outer) for poly in candidates]
        best, best_iou = None, 0.0
        for poly in candidates:
            score = _iou(poly, ref["polygon"])
            if score > best_iou:
                best, best_iou = poly, score
        scores.append(best_iou)
        if best is not None:
            objects.append({"id": ref["id"], "kind": ref["kind"], "polygon": best,
                            "gpu_indices": list(ref["gpu_indices"])})
    worst = min(scores) if scores else 0.0
    status = "ok" if worst >= IOU_MIN and len(objects) == len(manual["objects"]) else "cut"
    return {"source": "sam3.1", "image": manual["image"], "objects": objects,
            "iou_vs_manual": round(worst, 4), "status": status}
