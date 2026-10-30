#!/usr/bin/env python3
"""OFFLINE, ONE-TIME: draw the SYNTHETIC hotspot-set placeholder PNGs.

This makes NO network or paid call — it only renders small IR-style rack
images with PIL so the detector self-test and its tests have something to read.
The real images are produced by ``scripts/gen_vision_assets.py`` (paid, one
time) which overwrites these. Run locally to regenerate the committed
placeholders:

    python scripts/_make_hotspot_placeholders.py

Every image is drawn with a burned-in 'SYNTHETIC' watermark and a hot glow
over the faulted slot, matching the ground-truth hotspot box in scenes.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SET_DIR = ROOT / "data" / "vision" / "hotspots"


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def draw(scene: dict, w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h), (8, 11, 16))
    d = ImageDraw.Draw(img)
    # Rack outline + four slots (IR-dark background, cool slots).
    d.rounded_rectangle([160, 50, 470, 910], radius=8, outline=(79, 74, 99), width=3)
    slots = {0: 110, 1: 280, 2: 450, 3: 620}
    for s, y in slots.items():
        d.rounded_rectangle([190, y, 430, y + 140], radius=4, outline=(108, 94, 124),
                            fill=(26, 24, 40), width=2)
        d.text((200, y + 8), f"GPU {s}", fill=(142, 122, 158))
    # Hot glow over the faulted slot: concentric IR gradient (blue -> red -> yellow).
    x1, y1, x2, y2 = scene["hotspot"]["bbox"]
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    for r in range(130, 0, -6):
        t = 1.0 - (r / 130.0)
        col = _lerp((40, 30, 90), (255, 220, 60), t) if t > 0.5 else _lerp((20, 24, 60), (40, 30, 90), t * 2)
        d.ellipse([cx - r, cy - int(r * 0.62), cx + r, cy + int(r * 0.62)], fill=col)
    d.rectangle([x1, y1, x2, y2], outline=(224, 108, 117), width=3)
    d.text((x1 + 8, y1 + 8), scene["fault"].replace("_", " ").upper(), fill=(246, 219, 192))
    # Burned-in SYNTHETIC watermark (corner) + fault id.
    d.text((w - 150, h - 28), "SYNTHETIC", fill=(196, 180, 214))
    d.text((12, 12), f"IR THERMAL (SYNTHETIC) · {scene['id']}", fill=(142, 122, 158))
    return img


def main() -> int:
    manifest = json.loads((SET_DIR / "scenes.json").read_text(encoding="utf-8"))
    w = int(manifest["canvas"]["w"])
    h = int(manifest["canvas"]["h"])
    for scene in manifest["scenes"]:
        img = draw(scene, w, h)
        out = SET_DIR / scene["file"]
        img.save(out, "PNG", optimize=True)
        print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
