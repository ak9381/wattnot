"""Rack floor-map overlays.

The manual overlay is the fallback: polygons drawn by hand on one rack photo.
Loading it checks the file locally. It does not call a model.
"""

from __future__ import annotations

from wattnot.sam.compare import polygon_iou
from wattnot.sam.hotspot_set import (
    FAULTS,
    HotspotScene,
    HotspotSet,
    grade_hotspots,
    hotspot_set_gate,
    load_hotspot_set,
    run_hotspot_set,
)
from wattnot.sam.rackmap import (
    FALLBACK_BANNER,
    gpu_watts_from_trace,
    render_svg,
    slot_regions,
)
from wattnot.sam.segment import (
    MasksError,
    image_size,
    load_manual_overlay,
    load_masks,
    manual_overlay_path,
    validate_masks,
)

__all__ = [
    "FALLBACK_BANNER",
    "FAULTS",
    "HotspotScene",
    "HotspotSet",
    "grade_hotspots",
    "hotspot_set_gate",
    "load_hotspot_set",
    "run_hotspot_set",
    "gpu_watts_from_trace",
    "render_svg",
    "slot_regions",
    "MasksError",
    "image_size",
    "load_manual_overlay",
    "load_masks",
    "manual_overlay_path",
    "polygon_iou",
    "validate_masks",
]
