"""Plot a sweep as joules-per-correct-answer vs quality, kept green / rejected red.

``build_points`` normalises the sweep history (the same per-trial dicts the Wonder
planner produces) into ``ChartPoint``s. ``render_chart`` / ``chart_png_bytes`` draw
the scatter to a PNG with Pillow — green dots for kept trials, red for rejected,
axes labelled, and an ESTIMATE/SYNTHETIC footer so the figure is never mistaken for
a measurement. The Pillow import is inside the drawing functions, so importing this
module (and everything downstream: the reader, the receipt block, the tests) needs
no image library. ``points_sha256`` gives a stable hash of the plotted data when no
PNG was rendered, so the receipt always carries a hash-covered pointer to the chart.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# ESTIMATE energy stamp for one chart read (render + the vision call). Not metered;
# always shown as ESTIMATE on the receipt and in the UI.
EST_WH_CHART = 0.5

GREEN = (46, 160, 67)
RED = (200, 40, 40)
INK = (220, 214, 228)
MUTE = (142, 122, 158)
BG = (31, 26, 47)
GRID = (70, 60, 86)

_SETTING = re.compile(r"[^0-9A-Za-z/.\- ]+")


@dataclass(frozen=True)
class ChartPoint:
    """One trial on the chart. ``kept`` picks the colour (green kept, red rejected)."""

    setting: str
    cap_w: int
    clock_profile: str
    joules_per_correct: float
    quality: float
    kept: bool
    label: str = "MEASURED"

    def as_dict(self) -> dict:
        return {
            "setting": self.setting[:48],
            "cap_w": int(self.cap_w),
            "clock_profile": str(self.clock_profile)[:32],
            "joules_per_correct": round(float(self.joules_per_correct), 6),
            "quality": round(float(self.quality), 6),
            "kept": bool(self.kept),
            "label": str(self.label)[:32],
        }


def _first(trial: Mapping, *keys: str, default=None):
    for k in keys:
        if k in trial and trial[k] is not None:
            return trial[k]
    return default


def build_points(trials: Sequence[Mapping]) -> list[ChartPoint]:
    """Normalise varied sweep-history dicts into ``ChartPoint``s.

    Accepts the Wonder-planner trial shape (``joules_per_correct``, ``quality``,
    ``accepted``/``kept``) as well as the harness history shape
    (``energy_change_pct``, ``quality_pass_pct``, ``accepted``). Rejected trials
    keep their measured quality for the plot; whether a point is kept or rejected
    is the colour, not the y value.
    """

    points: list[ChartPoint] = []
    for t in trials:
        cap = int(_first(t, "cap_w", "power_cap_w", default=0) or 0)
        clock = str(_first(t, "clock_profile", "clock profile", default="base") or "base")

        jpc = _first(t, "joules_per_correct", "joules per correct")
        if jpc is None:
            # harness shape: relative joules from the energy change vs stock
            jpc = 1.0 + float(_first(t, "energy_change_pct", default=0.0) or 0.0) / 100.0
        jpc = float(jpc)

        q = _first(t, "quality", "quality_pass_pct")
        if q is None:
            q = 1.0
        q = float(q)
        if q > 1.0:  # a pass percentage (0..100) -> fraction
            q = q / 100.0

        kept = bool(_first(t, "kept", "accepted", default=False))
        label = str(_first(t, "label", default="MEASURED") or "MEASURED")
        setting = str(_first(t, "setting", default="") or f"{cap}W/{clock}")
        setting = _SETTING.sub("", setting)
        points.append(ChartPoint(setting, cap, clock, jpc, q, kept, label))
    return points


def points_sha256(points: Sequence[ChartPoint]) -> str:
    """Stable hash of the plotted data (used when no PNG was rendered)."""

    blob = json.dumps([p.as_dict() for p in points], separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def chart_sha256(png: bytes) -> str:
    return hashlib.sha256(png).hexdigest()


def _scale(v: float, lo: float, hi: float, a: int, b: int) -> float:
    if hi <= lo:
        return (a + b) / 2
    return a + (v - lo) / (hi - lo) * (b - a)


def chart_png_bytes(points: Sequence[ChartPoint], *,
                    title: str = "Joules per correct answer vs quality",
                    size: tuple[int, int] = (760, 520)) -> bytes:
    """Render the scatter to PNG bytes. Needs Pillow (``pip install wattnot[vision]``)."""

    from PIL import Image, ImageDraw, ImageFont

    w, h = size
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 15)
        small = ImageFont.truetype("DejaVuSans.ttf", 12)
        bold = ImageFont.truetype("DejaVuSans-Bold.ttf", 17)
    except OSError:
        font = small = bold = ImageFont.load_default()

    margin_l, margin_r, margin_t, margin_b = 70, 24, 54, 66
    x0, y0, x1, y1 = margin_l, margin_t, w - margin_r, h - margin_b
    d.text((margin_l, 16), title, fill=INK, font=bold)

    pts = list(points)
    xs = [p.joules_per_correct for p in pts] or [0.0, 1.0]
    ys = [p.quality for p in pts] or [0.0, 1.0]
    xlo, xhi = min(xs), max(xs)
    ylo, yhi = min(ys), max(ys)
    # pad the ranges a little so points never sit on the frame
    xpad = (xhi - xlo) * 0.08 or 0.1
    ypad = (yhi - ylo) * 0.08 or 0.05
    xlo, xhi = xlo - xpad, xhi + xpad
    ylo, yhi = max(0.0, ylo - ypad), min(1.0, yhi + ypad) if yhi + ypad > 1 else yhi + ypad

    # frame + light grid
    d.rectangle([x0, y0, x1, y1], outline=MUTE, width=1)
    for i in range(1, 4):
        gy = y0 + (y1 - y0) * i / 4
        gx = x0 + (x1 - x0) * i / 4
        d.line([(x0, gy), (x1, gy)], fill=GRID, width=1)
        d.line([(gx, y0), (gx, y1)], fill=GRID, width=1)

    # axis labels + ticks
    d.text((x0, y1 + 30), "joules per correct answer  (lower is better)",
           fill=MUTE, font=small)
    for frac in (0.0, 0.5, 1.0):
        xv = xlo + (xhi - xlo) * frac
        px = _scale(xv, xlo, xhi, x0, x1)
        d.text((px - 12, y1 + 6), f"{xv:.2f}", fill=MUTE, font=small)
        yv = ylo + (yhi - ylo) * frac
        py = _scale(yv, ylo, yhi, y1, y0)
        d.text((8, py - 7), f"{yv:.2f}", fill=MUTE, font=small)
    # rotated y-axis label
    ylab = Image.new("RGBA", (180, 20), (0, 0, 0, 0))
    ImageDraw.Draw(ylab).text((0, 0), "quality (0-1, higher is better)", fill=MUTE, font=small)
    img.paste(ylab.rotate(90, expand=True), (6, (y0 + y1) // 2 - 90), ylab.rotate(90, expand=True))

    # points
    r = 7
    for p in pts:
        px = _scale(p.joules_per_correct, xlo, xhi, x0, x1)
        py = _scale(p.quality, ylo, yhi, y1, y0)
        color = GREEN if p.kept else RED
        d.ellipse([px - r, py - r, px + r, py + r], fill=color, outline=INK, width=1)
        d.text((px + r + 2, py - 7), str(p.cap_w), fill=INK, font=small)

    # legend
    lx, ly = x1 - 150, y0 + 8
    d.ellipse([lx, ly, lx + 12, ly + 12], fill=GREEN, outline=INK, width=1)
    d.text((lx + 18, ly - 1), "kept", fill=INK, font=small)
    d.ellipse([lx, ly + 20, lx + 12, ly + 32], fill=RED, outline=INK, width=1)
    d.text((lx + 18, ly + 19), "rejected", fill=INK, font=small)

    labels = "/".join(sorted({p.label for p in pts})) or "MOCK"
    d.text((margin_l, h - 22),
           f"SYNTHETIC figure — plot of {labels} trial data, not a measurement. "
           f"read energy ~{EST_WH_CHART} Wh ESTIMATE.", fill=MUTE, font=small)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_chart(points: Sequence[ChartPoint], out: str | Path, *,
                 title: str = "Joules per correct answer vs quality") -> Path:
    """Render the chart and write it to ``out``. Returns the path."""

    png = chart_png_bytes(points, title=title)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png)
    return out


__all__ = [
    "EST_WH_CHART",
    "ChartPoint",
    "build_points",
    "chart_png_bytes",
    "chart_sha256",
    "points_sha256",
    "render_chart",
]
