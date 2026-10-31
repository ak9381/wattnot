"""Image-acceptance probe: prove the served model really reads an image.

    python -m wattnot.vision.imageprobe --base-url http://127.0.0.1:8000/v1
    python -m wattnot.vision.imageprobe --fake          # dry-run, no network

Muse Glimmer 30B reads text + one image per request. Before the eye test spends
any budget, this sends ONE small real image (a digit drawn with PIL, inlined as
a data URI on the ``/chat/completions`` chat route, exactly as the eye test does)
and checks the server accepts it and answers. If the server was started
without the multimodal flag it rejects the image (HTTP 400 / "not supported"),
and this probe fails loudly with a clear message instead of letting the eye test
quietly score every image as a miss.

Exit codes: 0 accepted (and, live, a non-empty answer came back); 2 the image was
rejected or the call failed; 3 bad arguments. ``--fake`` draws the image and
exits 0 without any network call, so the dry run exercises the plumbing only.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import urllib.error
import urllib.request

# A known digit the probe draws and asks the model to read. Acceptance (the server
# took the image) is the pass condition; the answer is reported but a wrong answer
# does not fail the probe here — the eye test is the quality gate.
PROBE_DIGIT = "7"
_INSTRUCTION = "Answer with only the digit, no explanation."
# Glimmer reasons before answering; a small cap left content empty. Configurable so a bigger
# reasoner can be given more room without a code change. See wattnot.vision.client.
REASONING_MAX_COMPLETION_TOKENS = int(os.environ.get("WATTNOT_REASONING_MAX_TOKENS", "1024"))


def _digit_font(px: int):
    """A large font for the probe digit: a bundled TrueType face if available, else the default.

    The old probe used PIL's tiny default bitmap font (~11 px) on a 96 px canvas; the digit was
    too small to read and the model answered 'None' (RUN3). Draw it big and bold instead."""
    from PIL import ImageFont

    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=px)  # Pillow >= 10.1 can size the default font
    except TypeError:
        return ImageFont.load_default()


def _small_image_png() -> bytes:
    """A real PNG with one big, clearly legible digit on white (drawn with PIL)."""
    from PIL import Image, ImageDraw

    size = 220
    img = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(img)
    font = _digit_font(150)
    # centre the glyph using its bounding box, so it fills the frame and is easy to read
    try:
        x0, y0, x1, y1 = d.textbbox((0, 0), PROBE_DIGIT, font=font)
    except (AttributeError, TypeError):
        x0, y0, x1, y1 = 0, 0, 90, 150
    d.text(((size - (x1 - x0)) / 2 - x0, (size - (y1 - y0)) / 2 - y0), PROBE_DIGIT,
           fill=(0, 0, 0), font=font)
    d.rectangle([3, 3, size - 4, size - 4], outline=(0, 0, 0), width=3)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _body(model: str, data_uri: str, max_completion_tokens: int) -> dict:
    return {
        "model": model,
        "max_completion_tokens": max_completion_tokens,  # reasoning: a small cap empties content
        "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": f"What digit is shown in the image? {_INSTRUCTION}"},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]}],
    }


def probe(base_url: str, model: str, *, timeout_s: float = 60.0,
          max_completion_tokens: int = REASONING_MAX_COMPLETION_TOKENS) -> tuple[bool, str]:
    """Send one real image to the served model. Returns (accepted, detail)."""
    body = _body(model, _data_uri(_small_image_png()), max_completion_tokens)
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:  # noqa: S310
            data = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300] if hasattr(e, "read") else str(e)
        return False, f"HTTP {e.code}: {detail}"
    except Exception as e:  # noqa: BLE001 - any transport failure is a clear reject
        return False, f"{type(e).__name__}: {str(e)[:200]}"
    try:
        answer = str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError):
        return False, f"no answer in response: {json.dumps(data)[:200]}"
    if not answer:
        return False, "server accepted the request but returned an empty answer"
    return True, f"accepted; answered {answer!r} (drew {PROBE_DIGIT!r})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="WattNot image-acceptance probe")
    ap.add_argument("--base-url", default=None, help="served model base URL (e.g. http://IP:8000/v1)")
    ap.add_argument("--model", default="meta-models/Muse-Glimmer-30B")
    ap.add_argument("--fake", action="store_true", help="dry-run: draw the image, make no call")
    ap.add_argument("--timeout-s", type=float, default=60.0)
    ap.add_argument("--max-completion-tokens", type=int, default=REASONING_MAX_COMPLETION_TOKENS,
                    help="completion-token cap (reasoning models need room for thinking + answer)")
    a = ap.parse_args(argv)

    if a.fake:
        png = _small_image_png()
        print(f"image probe [DRY-RUN]: drew a {len(png)}-byte PNG of {PROBE_DIGIT!r}; no call made")
        return 0
    if not a.base_url:
        print("image probe: --base-url is required for a live probe (or pass --fake)",
              file=sys.stderr)
        return 3
    ok, detail = probe(a.base_url, a.model, timeout_s=a.timeout_s,
                       max_completion_tokens=a.max_completion_tokens)
    print(f"image probe [{'OK' if ok else 'FAIL'}]: {detail}", file=sys.stderr if not ok else sys.stdout)
    if not ok:
        print("  the served model did not accept the image. Start it with multimodal enabled: "
              "glimmer_serve.sh sets --limit-mm-per-prompt.image 1 (LIMIT_MM_IMAGE).",
              file=sys.stderr)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
