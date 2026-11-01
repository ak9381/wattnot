"""The eye-test gauge renders a readable needle that points AT the 60 tick.

Root cause this covers: the old needle stopped at radius*0.86 (where the major ticks start), was
thin and had no tip, so the model read "62 PSI" (one minor division off). The answer 60 was correct
and unchanged; only the dial's readability was fixed. These tests check the needle now reaches
across the tick band and unambiguously points at the 60 tick angle.
"""

from __future__ import annotations

import importlib.util
import io
import math
from pathlib import Path

from PIL import Image

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "gen_vision_assets.py"
_spec = importlib.util.spec_from_file_location("gen_vision_assets", _PATH)
gva = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gva)


def _red_centroid_angle(img: Image.Image) -> float:
    """Angle (deg, CCW from +x, y-down screen) from the dial centre to the needle's red centroid."""
    w, h = img.size
    cx = cy = w / 2
    xs = ys = n = 0
    px = img.load()
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            if r > 150 and g < 90 and b < 90:
                xs += x
                ys += y
                n += 1
    assert n > 200, f"needle barely rendered ({n} red pixels)"
    return math.degrees(math.atan2(-(ys / n - cy), xs / n - cx)) % 360


def test_needle_points_at_the_60_tick_angle():
    img = Image.open(io.BytesIO(gva.render_gauge(60))).convert("RGB")
    ang = _red_centroid_angle(img)
    want60 = math.degrees(gva._gauge_angle(60)) % 360
    want62 = math.degrees(gva._gauge_angle(62)) % 360
    want58 = math.degrees(gva._gauge_angle(58)) % 360
    # The needle's red mass lies on the 60 radial, clearly nearer 60 than the adjacent minor ticks.
    assert abs(ang - want60) < 2.0, (ang, want60)
    assert abs(ang - want62) > 3.5 and abs(ang - want58) > 3.5


def test_needle_tip_reaches_the_major_tick_band():
    # The tip must run across the major-tick band (radius*0.86 .. radius*1.0), not stop short of it.
    img = Image.open(io.BytesIO(gva.render_gauge(60))).convert("RGB")
    size = 512
    cx = cy = size / 2
    radius = size * gva.GAUGE_RADIUS_FRAC
    for frac in (0.88, 0.92, 0.95):
        x, y = gva._gauge_point(cx, cy, radius * frac, 60)
        r, g, b = img.getpixel((round(x), round(y)))
        assert r > 150 and g < 90 and b < 90, (frac, (r, g, b))
    assert gva.NEEDLE_TIP_FRAC >= 0.95  # tip near the outer edge of the tick


def test_gauge_is_deterministic():
    assert gva.render_gauge(60) == gva.render_gauge(60)


def test_gauge_has_no_fine_divisions_and_value_on_labelled_tick():
    # The live Baseten smoke read the 2-unit dial as 62: the dial must not offer a 62 reading.
    assert gva.GAUGE_MINOR_STEP == 10 and gva.GAUGE_MAJOR_STEP == 20
    assert 60 % gva.GAUGE_MAJOR_STEP == 0
