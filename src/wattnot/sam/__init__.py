"""Rack floor-map overlays.

The manual overlay is the fallback: polygons drawn by hand on one rack photo.
Loading it checks the file locally. It does not call a model.
"""

from __future__ import annotations

from wattnot.sam.compare import polygon_iou
from wattnot.sam.segment import (
    MasksError,
    image_size,
    load_manual_overlay,
    load_masks,
    manual_overlay_path,
    validate_masks,
)

__all__ = [
    "MasksError",
    "image_size",
    "load_manual_overlay",
    "load_masks",
    "manual_overlay_path",
    "polygon_iou",
    "validate_masks",
]
