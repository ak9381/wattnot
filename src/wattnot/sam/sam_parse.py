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


def parse_boxes(text: str) -> list[dict[str, float]]:
    """Every box in a SAM output line, as floats keyed x1, y1, x2, y2, w, h (and c)."""

    boxes = []
    for match in _BOX_RE.finditer(text):
        fields = dict(part.split("=", 1) for part in match.group(1).split(";") if "=" in part)
        boxes.append({k: float(v) for k, v in fields.items()})
    return boxes


def box_polygon(box: Mapping[str, float]) -> list[list[float]]:
    x1, y1, x2, y2 = box["x1"], box["y1"], box["x2"], box["y2"]
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _iou(a: Sequence, b: Sequence) -> float:
    try:
        return polygon_iou(a, b)
    except ValueError:
        return 0.0


def build_document(raw_by_kind: Mapping[str, str], manual: Mapping[str, Any]) -> dict[str, Any]:
    """Build a `source: sam3.1` document from raw SAM output per kind."""

    objects: list[dict[str, Any]] = []
    scores: list[float] = []
    for ref in manual["objects"]:
        candidates = [box_polygon(b) for b in parse_boxes(raw_by_kind.get(ref["kind"], ""))]
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
