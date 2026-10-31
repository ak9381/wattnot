"""The image-acceptance probe draws a legible digit and takes a configurable token cap (RUN3)."""

from __future__ import annotations

import io

from wattnot.vision import imageprobe


def test_probe_draws_a_large_legible_png():
    png = imageprobe._small_image_png()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    from PIL import Image
    img = Image.open(io.BytesIO(png))
    # a readable digit needs a real canvas, not the old tiny 96px default-font frame
    assert min(img.size) >= 200
    # there is dark ink (the digit + border) on the white field
    assert img.convert("L").getextrema()[0] < 80


def test_probe_body_token_cap_is_configurable():
    body = imageprobe._body("m", "data:image/png;base64,AAAA", 1536)
    assert body["max_completion_tokens"] == 1536


def test_probe_fake_exits_zero():
    assert imageprobe.main(["--fake"]) == 0
