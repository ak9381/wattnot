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
import json
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
    "gauge-reading": "an analogue pressure gauge whose needle points at the number 63",
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


def main(argv: list[str] | None = None) -> int:
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
        webp = _post(base_url, key, prompt)
        out_name = f"{item['id']}.webp"
        (ASSETS / out_name).write_bytes(webp)
        item["file"] = out_name
        item["watermark"] = "SYNTHETIC"
        print(f"  wrote {out_name} ({len(webp)} bytes)")
    manifest["label"] = "SYNTHETIC"
    manifest["generator"] = {"model": MODEL, "endpoint": "{META_BASE_URL}/images/generations",
                             "format": "b64 webp", "label": "SYNTHETIC"}
    (ASSETS / "answers.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("done; rewrote answers.json (all SYNTHETIC)")

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
        webp = _post(base_url, key, f"{prompt}; {scene.get('scene', '')}")
        out_name = f"{scene['id']}.webp"
        (HOTSPOTS / out_name).write_bytes(webp)
        scene["file"] = out_name
        scene["watermark"] = "SYNTHETIC"
        print(f"  wrote {out_name} ({len(webp)} bytes)")
    scenes["label"] = "SYNTHETIC"
    scenes["generator"] = {"model": MODEL, "endpoint": "{META_BASE_URL}/images/generations",
                           "format": "b64 webp", "label": "SYNTHETIC"}
    (HOTSPOTS / "scenes.json").write_text(json.dumps(scenes, indent=2) + "\n", encoding="utf-8")
    print("done; rewrote scenes.json (all SYNTHETIC)")


if __name__ == "__main__":
    raise SystemExit(main())
