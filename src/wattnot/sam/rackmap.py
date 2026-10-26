"""Rack map: put each GPU's measured watts on the rack photo.

Takes a mask document (manual or SAM, same shape), a set of per-GPU watts
from a telemetry trace, and the GPUs a run touched. Writes an SVG with the
photo underneath, every slot outlined, touched slots filled and labelled
with their watts, and a banner when the manual fallback is in use.
SVG keeps this free of image libraries; any browser renders it.
"""

from __future__ import annotations

import base64
import html
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from wattnot.meter.base import read_trace
from wattnot.sam.segment import image_size

FALLBACK_BANNER = "MANUAL FALLBACK (SAM attempt in repo)"
_COLORS = {"rack": "#7aa2ff", "pdu": "#c792ea", "slot": "#5ad1e6"}


def gpu_watts_from_trace(path: str | Path) -> dict[int, float]:
    """Mean power per GPU over a 10 Hz trace CSV."""

    sums: dict[int, list[float]] = {}
    for row in read_trace(Path(path)):
        sums.setdefault(row.gpu, []).append(row.power_w)
    return {gpu: sum(v) / len(v) for gpu, v in sorted(sums.items())}


def slot_regions(doc: Mapping[str, Any], watts: Mapping[int, float],
                 touched: Iterable[int]) -> list[dict[str, Any]]:
    """One entry per slot: GPU, polygon, watts (None if not measured), touched."""

    touched = set(touched)
    out = []
    for obj in doc["objects"]:
        if obj["kind"] != "slot":
            continue
        gpu = obj["gpu_indices"][0]
        out.append({"id": obj["id"], "gpu": gpu, "polygon": obj["polygon"],
                    "watts": watts.get(gpu), "touched": gpu in touched})
    return out


def _points(poly) -> str:
    return " ".join(f"{x:g},{y:g}" for x, y in poly)


def render_svg(doc: Mapping[str, Any], image_path: str | Path, watts: Mapping[int, float],
               touched: Iterable[int], out_path: str | Path, *, fallback: bool,
               note: str | None = None) -> Path:
    image_path = Path(image_path)
    width, height = image_size(image_path)
    b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="monospace">',
        f'<image href="data:image/jpeg;base64,{b64}" width="{width}" height="{height}"/>',
    ]
    for obj in doc["objects"]:
        if obj["kind"] == "slot":
            continue
        color = _COLORS[obj["kind"]]
        parts.append(f'<polygon points="{_points(obj["polygon"])}" fill="none" '
                     f'stroke="{color}" stroke-width="2" stroke-dasharray="6 4"/>')
    for region in slot_regions(doc, watts, touched):
        hot = region["touched"]
        fill = "rgba(90,209,230,0.35)" if hot else "none"
        parts.append(f'<polygon points="{_points(region["polygon"])}" fill="{fill}" '
                     f'stroke="{_COLORS["slot"]}" stroke-width="{3 if hot else 1}"/>')
        if hot:
            x = min(p[0] for p in region["polygon"]) + 8
            y = max(p[1] for p in region["polygon"]) - 10
            w = region["watts"]
            label = f"GPU {region['gpu']}  " + (f"{w:.0f} W" if w is not None else "no data")
            parts.append(f'<text x="{x:g}" y="{y:g}" font-size="22" fill="#fff" '
                         f'stroke="#000" stroke-width="0.6">{html.escape(label)}</text>')
    source = doc["source"]
    caption = FALLBACK_BANNER if fallback else f"source: {source}"
    if note:
        caption += f" · {note}"
    parts.append(f'<rect x="0" y="0" width="{width}" height="30" fill="rgba(0,0,0,0.6)"/>'
                 f'<text x="10" y="21" font-size="16" fill="#ffd166">'
                 f'{html.escape(caption)}</text>')
    parts.append("</svg>")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return out_path
