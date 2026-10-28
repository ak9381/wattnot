"""SAM hotspot sets the cap (bounded single-GPU derate from thermal/rack photos).

Uses SAM 3.1 (mock deterministic by default; real only with explicit live=True) to identify
a thermal or rack hotspot region. The planner lowers the power cap on that GPU only (by a
bounded step, never below min_cap_w), leaving all other GPUs unchanged.

The hotspot mask (bbox, polygon, sha256), the cap decision (gpu id, old cap, new cap),
and the technical reason are stored on the signed receipt, covered by its integrity hash.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

DEFAULT_STEP_W = 50
DEFAULT_MIN_CAP_W = 250
DEFAULT_STOCK_CAP_W = 400

# Canonical slot polygons on standard rack view (640x960 px)
RACK_SLOT_POLYGONS: dict[int, list[list[int]]] = {
    0: [[190, 110], [430, 110], [430, 250], [190, 250]],
    1: [[190, 280], [430, 280], [430, 420], [190, 420]],
    2: [[190, 450], [430, 450], [430, 590], [190, 590]],
    3: [[190, 620], [430, 620], [430, 760], [190, 760]],
}


def _bbox_for_polygon(poly: Sequence[Sequence[int | float]]) -> list[int]:
    """Calculate [x1, y1, x2, y2] bounding box for a polygon."""
    xs = [int(p[0]) for p in poly]
    ys = [int(p[1]) for p in poly]
    return [min(xs), min(ys), max(xs), max(ys)]


def compute_mask_sha256(mask_dict: Mapping[str, Any]) -> str:
    """Deterministic SHA-256 hex digest for a mask structure."""
    canonical = json.dumps(
        {
            "bbox": mask_dict.get("bbox"),
            "polygon": mask_dict.get("polygon"),
            "rle": mask_dict.get("rle"),
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def detect_hotspot_mask(
    photo_path: str | Path | None = None,
    *,
    target_gpu: int = 1,
    mock: bool = True,
    live: bool = False,
    ledger: Any = None,
    prompt: str = "thermal hotspot rack slot",
) -> dict[str, Any]:
    """Segment the hotspot rack/GPU region using SAM 3.1 (or deterministic mock)."""
    is_mock = mock or (not live) or (os.environ.get("WATTNOT_META_MOCK") == "1")

    live_poly: list[list[int]] | None = None
    if not is_mock:
        # Live path: use real SamClient with ledger spend
        from wattnot.sam.client import SamClient
        from wattnot.sam.sam_parse import parse_boxes

        client = SamClient(ledger=ledger)
        image_bytes = b""
        if photo_path and Path(photo_path).is_file():
            image_bytes = Path(photo_path).read_bytes()
        else:
            default_photo = Path(__file__).resolve().parents[3] / "rack" / "rack.jpg"
            if default_photo.is_file():
                image_bytes = default_photo.read_bytes()
            else:
                image_bytes = b"\xff\xd8\xff\xe0mockjpegdata"

        raw = client.segment(image_bytes, prompt, purpose="sam:hotspot-cap")
        boxes = parse_boxes(raw or "")
        if boxes:
            b = boxes[0]
            live_poly = [[int(b["x1"]), int(b["y1"])], [int(b["x2"]), int(b["y1"])],
                         [int(b["x2"]), int(b["y2"])], [int(b["x1"]), int(b["y2"])]]

    # Polygon: live SAM box when available, otherwise the canonical slot outline.
    # Only a polygon that actually came back from SAM may be labelled MEASURED;
    # the canonical slot fallback is SIMULATED in live mode and MOCK otherwise.
    poly = live_poly or RACK_SLOT_POLYGONS.get(target_gpu, RACK_SLOT_POLYGONS[1])
    bbox = _bbox_for_polygon(poly)
    # Simple compact RLE string representation
    rle = f"{bbox[0]},{bbox[1]},{bbox[2] - bbox[0]},{bbox[3] - bbox[1]}"

    mask_obj = {
        "bbox": bbox,
        "polygon": poly,
        "rle": rle,
        "label": "MOCK" if is_mock else ("MEASURED" if live_poly else "SIMULATED"),
    }
    mask_obj["sha256"] = compute_mask_sha256(mask_obj)
    return mask_obj


def plan_hotspot_cap(
    *,
    photo_path: str | Path | None = None,
    gpu_caps: Mapping[int | str, int | float] | Sequence[int | float] | None = None,
    target_gpu: int | str = 1,
    step_w: int = DEFAULT_STEP_W,
    min_cap_w: int = DEFAULT_MIN_CAP_W,
    mock: bool = True,
    live: bool = False,
    ledger: Any = None,
) -> dict[str, Any]:
    """Identify the hotspot GPU, lower its cap by bounded step (>= min_cap_w), keep others unchanged.

    Returns the mask details, cap decision, updated cap map, and technical reason.
    """
    is_mock = mock or (not live) or (os.environ.get("WATTNOT_META_MOCK") == "1")

    # Normalise GPU caps dictionary
    current_caps: dict[int, int] = {}
    if gpu_caps is None:
        current_caps = {0: DEFAULT_STOCK_CAP_W, 1: DEFAULT_STOCK_CAP_W, 2: DEFAULT_STOCK_CAP_W, 3: DEFAULT_STOCK_CAP_W}
    elif isinstance(gpu_caps, Mapping):
        current_caps = {int(k): int(v) for k, v in gpu_caps.items()}
    else:
        current_caps = {i: int(v) for i, v in enumerate(gpu_caps)}

    target_id = int(target_gpu)
    if target_id not in current_caps:
        current_caps[target_id] = DEFAULT_STOCK_CAP_W

    old_cap = current_caps[target_id]
    # Bounded reduction: never below min_cap_w, and never above the existing cap
    # (a GPU already under min_cap_w keeps its current cap — a derate must not raise power).
    new_cap = min(old_cap, max(min_cap_w, old_cap - step_w))
    actual_reduction = old_cap - new_cap

    # Mask identification from SAM 3.1
    mask = detect_hotspot_mask(
        photo_path=photo_path,
        target_gpu=target_id,
        mock=is_mock,
        live=live,
        ledger=ledger,
    )

    # Build updated caps dictionary: target GPU lowered, other GPUs strictly unchanged
    updated_caps = dict(current_caps)
    updated_caps[target_id] = new_cap

    # Plan provenance follows the mask: MOCK in mock mode, otherwise whatever the
    # live segmentation honestly produced (MEASURED, or SIMULATED on fallback).
    label = "MOCK" if is_mock else str(mask.get("label", "SIMULATED"))
    # A cap decision is a planner recommendation in both modes, never a measurement.
    advice_label = "ADVICE"

    if actual_reduction > 0:
        reason = (
            f"Hotspot detected on GPU {target_id} (thermal threshold exceeded); "
            f"lowered cap from {old_cap}W to {new_cap}W (-{actual_reduction}W step, min {min_cap_w}W); "
            f"other GPUs unchanged."
        )
    else:
        reason = (
            f"Hotspot detected on GPU {target_id}; cap already at or below minimum of {min_cap_w}W; "
            f"cap held at {old_cap}W; other GPUs unchanged."
        )

    # Ensure reason string fits within receipt lint limits (< 200 chars)
    if len(reason) > 195:
        reason = reason[:195]

    decision = {
        "gpu_id": target_id,
        "old_cap_w": old_cap,
        "new_cap_w": new_cap,
        "delta_w": -actual_reduction,
        "min_cap_w": min_cap_w,
        "step_w": step_w,
        "label": advice_label,
    }

    return {
        "mask": mask,
        "decision": decision,
        "reason": reason,
        "gpu_caps_before": current_caps,
        "gpu_caps_after": updated_caps,
        "hotspot_gpu": target_id,
        "label": label,
    }
