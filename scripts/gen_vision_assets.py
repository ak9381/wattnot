#!/usr/bin/env python3
"""ONE-TIME PAID script: generate the eye-test images with Muse Image.

This is the only place a paid image-generation call lives. It is NEVER run by
the test suite or any automated flow — it needs an explicit go-ahead
(``WATTNOT_GEN_VISION_GO=1``) and a real API key, and it costs money.

It reads the prompts/answers from ``data/vision/eyetest/answers.json``, calls
``POST {META_BASE_URL}/images/generations`` with ``muse-image-1.0`` (b64 webp),
writes each image next to the manifest, and rewrites the manifest so every
entry points at the generated ``.webp`` and is labelled ``SYNTHETIC``.

    WATTNOT_GEN_VISION_GO=1 META_API_KEY=... python scripts/gen_vision_assets.py

Importing this module does nothing: no key is read and no call is made until
``main()`` runs. The committed ``.png`` files are offline placeholders so the
gate and its tests work without ever running this.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "data" / "vision" / "eyetest"
HOTSPOTS = ROOT / "data" / "vision" / "hotspots"
MODEL = "muse-image-1.0"

# One IR-thermal scene prompt per hotspot-set fault. Each must render a clear
# hot region over the faulted rack slot and burn in a SYNTHETIC watermark, so
# the SAM 3.1 hotspot detector has an honest-looking failure scene to find.
HOTSPOT_PROMPTS = {
    "blocked_fan": "an infrared thermal photo of a server rack where one GPU's intake fan is choked by debris, a bright red-yellow heat bloom over that slot, the rest of the rack cool blue",
    "clogged_heatsink": "an infrared thermal photo of a server rack where one GPU's heatsink fins are clogged with dust, a bright hot patch pinned to that card, neighbouring slots cool",
    "cable_airflow": "an infrared thermal photo of a server rack where a loose cable drapes across one slot and blocks airflow, a hot band glowing over that slot, other slots cool",
}

# A short scene prompt per item id. The scene must render the known answer
# legibly and be hard enough that a vision-quant collapse misreads it. Every
# prompt asks for a SYNTHETIC watermark burned into the pixels.
PROMPTS = {
    "plate-bus789": "a weathered vehicle number plate reading 'BUS-789' in small tight type, slight motion blur",
    "plate-van204": "a dusty delivery-van number plate reading 'VAN-204' in small type, shallow depth of field",
    "invoice-total": "a printed invoice whose TOTAL line reads '$1,284.50' in small monospace, other rows blurred",
    "invoice-subtotal": "a printed invoice whose SUBTOTAL line reads '$990.00' in small monospace",
    "count-cubes": "exactly seven identical wooden cubes scattered on a plain table, top-down",
    "count-bottles": "exactly four glass bottles in a row on a shelf, even lighting",
    "color-chart-top": "a two-row colour chart; the top swatch is clearly magenta, the bottom is grey",
    "color-chart-bottom": "a two-row colour chart; the bottom swatch is clearly teal, the top is grey",
    "gauge-reading": "an analogue pressure gauge whose needle points at the number 60",
    "sign-exit": "a green highway sign reading 'EXIT 12B' in small text, viewed at an angle",
}


def _post(base_url: str, key: str, prompt: str, timeout: float = 120) -> bytes:
    body = json.dumps({
        "model": MODEL,
        "prompt": f"{prompt}. Burn a small 'SYNTHETIC' watermark into a corner.",
        "n": 1,
        "size": "512x512",
        "response_format": "b64_json",
    }).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/images/generations", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        data = json.loads(r.read() or b"{}")
    return base64.b64decode(data["data"][0]["b64_json"])


_LEDGER = None


def _charge(purpose: str) -> str | None:
    """Record each image on the Meta spend ledger before sending ($0.01/image, MEASURED price)."""
    global _LEDGER
    if _LEDGER is None:
        from wattnot.meta.ledger import SpendLedger
        _LEDGER = SpendLedger()
    return _LEDGER.charge_image(MODEL, purpose)


def _load_font(size: int):
    """Best-effort TrueType bold font; fall back to PIL's default at ``size``."""
    from PIL import ImageFont

    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow: load_default() takes no size
        return ImageFont.load_default()


def render_gauge(value: int = 60, size: int = 512) -> bytes:
    """Draw a clean analog gauge (scale 0..100) with the needle dead-on ``value``.

    Deterministic: no randomness, no time, no network — identical bytes every run.
    Returns PNG bytes. The needle lands exactly on the labelled major tick for
    ``value`` so the reading is unambiguous.
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(img)

    cx = cy = size / 2
    radius = size * 0.42
    # Sweep the 0..100 scale over a 270-degree arc. 0 at lower-left (225 deg
    # measured anticlockwise from +x in screen space, i.e. pointing down-left),
    # sweeping clockwise through the top to 100 at lower-right.
    start_deg = 225.0  # angle (CCW from +x, y-down screen) for value 0
    sweep_deg = -270.0  # clockwise sweep across the full scale

    def angle_for(v: float) -> float:
        return math.radians(start_deg + sweep_deg * (v / 100.0))

    def point(v: float, r: float) -> tuple[float, float]:
        a = angle_for(v)
        # Screen coords: y grows downward, so subtract the sine term.
        return (cx + r * math.cos(a), cy - r * math.sin(a))

    # Dial face: outer circle + a faint inner ring.
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                 outline="black", width=max(3, size // 160))
    inner = radius * 0.80
    draw.ellipse([cx - inner, cy - inner, cx + inner, cy + inner],
                 outline=(180, 180, 180), width=max(1, size // 400))

    num_font = _load_font(int(size * 0.072))
    unit_font = _load_font(int(size * 0.06))

    # Ticks: minor every 2, major every 10 with a large bold numeral.
    for v in range(0, 101, 2):
        is_major = (v % 10 == 0)
        r_outer = radius
        r_inner = radius - (radius * (0.14 if is_major else 0.07))
        x1, y1 = point(v, r_inner)
        x2, y2 = point(v, r_outer)
        draw.line([x1, y1, x2, y2], fill="black",
                  width=max(3, size // 150) if is_major else max(1, size // 350))
        if is_major:
            lx, ly = point(v, radius * 0.64)
            label = str(v)
            bbox = draw.textbbox((0, 0), label, font=num_font)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text((lx - w / 2 - bbox[0], ly - h / 2 - bbox[1]), label,
                      fill="black", font=num_font)

    # Needle: bold line from centre to just inside the target tick + centre hub.
    nx, ny = point(value, radius * 0.86)
    draw.line([cx, cy, nx, ny], fill=(200, 0, 0), width=max(4, size // 90))
    hub = max(6, size // 42)
    draw.ellipse([cx - hub, cy - hub, cx + hub, cy + hub], fill="black")

    # Unit label under the hub.
    unit = "PSI"
    bbox = draw.textbbox((0, 0), unit, font=unit_font)
    uw = bbox[2] - bbox[0]
    draw.text((cx - uw / 2 - bbox[0], cy + radius * 0.34), unit,
              fill=(90, 90, 90), font=unit_font)

    # Small grey SYNTHETIC watermark in the bottom-left corner.
    wm_font = _load_font(int(size * 0.032))
    draw.text((size * 0.02, size * 0.955), "SYNTHETIC",
              fill=(150, 150, 150), font=wm_font)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _gen_gauge() -> int:
    """Regenerate the gauge image deterministically with PIL (no paid call, no gate).

    Writes gauge-reading.png + .webp and updates the gauge item in answers.json
    with honest per-item provenance and the .webp sha256.
    """
    from PIL import Image

    value = 60
    png = render_gauge(value)
    (ASSETS / "gauge-reading.png").write_bytes(png)
    # Re-encode the PNG to WEBP via PIL (deterministic lossless).
    img = Image.open(io.BytesIO(png)).convert("RGB")
    img.save(ASSETS / "gauge-reading.webp", "WEBP", lossless=True)
    webp_bytes = (ASSETS / "gauge-reading.webp").read_bytes()
    sha = hashlib.sha256(webp_bytes).hexdigest()

    manifest = json.loads((ASSETS / "answers.json").read_text(encoding="utf-8"))
    note = ("Rendered deterministically by scripts/gen_vision_assets.py --gauge "
            "with PIL (not a photo, not the image model); needle on the labelled "
            "60 tick.")
    for item in manifest["items"]:
        if item["id"] == "gauge-reading":
            item["file"] = "gauge-reading.webp"
            item["answer"] = str(value)
            item["source"] = "RENDERED-PIL"
            item["source_note"] = note
            item["sha256"] = sha
            item["watermark"] = "SYNTHETIC"
            break
    else:
        print("no gauge-reading item in manifest", file=sys.stderr)
        return 1
    (ASSETS / "answers.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote gauge-reading.png ({len(png)} bytes) and gauge-reading.webp "
          f"({len(webp_bytes)} bytes); answer=60 sha256={sha}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--gauge" in argv:
        return _gen_gauge()
    if os.environ.get("WATTNOT_GEN_VISION_GO") != "1":
        print("refusing: this is a PAID one-time script. Set WATTNOT_GEN_VISION_GO=1 to run.",
              file=sys.stderr)
        return 2
    # Imported lazily so this module stays importable without the meta package wired.
    from wattnot.meta.client import load_key
    key = load_key()
    if not key:
        print("no API key: set META_API_KEY or log in with `muse login`", file=sys.stderr)
        return 2
    base_url = os.environ.get("META_BASE_URL", "https://api.meta.ai/v1")

    manifest = json.loads((ASSETS / "answers.json").read_text(encoding="utf-8"))
    print(f"PAID: generating {len(manifest['items'])} images with {MODEL} at {base_url}")
    for item in manifest["items"]:
        prompt = PROMPTS.get(item["id"])
        if not prompt:
            print(f"  skip {item['id']}: no prompt", file=sys.stderr)
            continue
        eid = _charge(f"vision-gen:{item['id']}")
        try:
            webp = _post(base_url, key, prompt)
        except Exception:
            _LEDGER.refund(eid, "generation failed")
            raise
        out_name = f"{item['id']}.webp"
        (ASSETS / out_name).write_bytes(webp)
        item["file"] = out_name
        item["watermark"] = "SYNTHETIC"
        print(f"  wrote {out_name} ({len(webp)} bytes)")
    manifest["label"] = "SYNTHETIC"
    # Honest provenance: these are now real Muse Image generations, so the no-mock guard accepts
    # them for a live run (it rejects source=placeholder-offline).
    manifest["source"] = "GENERATED-META"
    manifest["source_note"] = f"Real images generated by {MODEL} (Meta); SYNTHETIC content, real files."
    manifest["generator"] = {"model": MODEL, "endpoint": "{META_BASE_URL}/images/generations",
                             "format": "b64 webp", "label": "SYNTHETIC"}
    (ASSETS / "answers.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("done; rewrote answers.json (SYNTHETIC content, source=GENERATED-META)")

    _gen_hotspots(base_url, key)
    return 0


def _gen_hotspots(base_url: str, key: str) -> None:
    """Generate the SAM synthetic hotspot scenes (IR thermal failures)."""

    scenes = json.loads((HOTSPOTS / "scenes.json").read_text(encoding="utf-8"))
    print(f"PAID: generating {len(scenes['scenes'])} hotspot scenes with {MODEL} at {base_url}")
    for scene in scenes["scenes"]:
        prompt = HOTSPOT_PROMPTS.get(scene["fault"])
        if not prompt:
            print(f"  skip {scene['id']}: no prompt for fault {scene['fault']!r}", file=sys.stderr)
            continue
        eid = _charge(f"vision-gen:{scene['id']}")
        try:
            webp = _post(base_url, key, f"{prompt}; {scene.get('scene', '')}")
        except Exception:
            _LEDGER.refund(eid, "generation failed")
            raise
        out_name = f"{scene['id']}.webp"
        (HOTSPOTS / out_name).write_bytes(webp)
        scene["file"] = out_name
        scene["watermark"] = "SYNTHETIC"
        print(f"  wrote {out_name} ({len(webp)} bytes)")
    scenes["label"] = "SYNTHETIC"
    scenes["source"] = "GENERATED-META"
    scenes["source_note"] = f"Real images generated by {MODEL} (Meta); SYNTHETIC content, real files."
    scenes["generator"] = {"model": MODEL, "endpoint": "{META_BASE_URL}/images/generations",
                           "format": "b64 webp", "label": "SYNTHETIC"}
    (HOTSPOTS / "scenes.json").write_text(json.dumps(scenes, indent=2) + "\n", encoding="utf-8")
    print("done; rewrote scenes.json (SYNTHETIC content, source=GENERATED-META)")


if __name__ == "__main__":
    raise SystemExit(main())
