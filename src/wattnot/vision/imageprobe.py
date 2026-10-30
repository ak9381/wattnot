"""Image-acceptance probe: prove the served model really reads an image.

    python -m wattnot.vision.imageprobe --base-url http://127.0.0.1:8000/v1
    python -m wattnot.vision.imageprobe --fake          # dry-run, no network

Muse Glimmer 30B reads text + one image per request. Before the eye test spends
any budget, this sends ONE small real image (a digit drawn with PIL, inlined as
a data URI on the OpenAI ``/chat/completions`` route, exactly as the eye test
does) and checks the server accepts it and answers. If the server was started
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
import sys
import urllib.error
import urllib.request

# A known digit the probe draws and asks the model to read. Acceptance (the server
# took the image) is the pass condition; the answer is reported but a wrong answer
# does not fail the probe here — the eye test is the quality gate.
PROBE_DIGIT = "7"
_INSTRUCTION = "Answer with only the digit, no explanation."


def _small_image_png() -> bytes:
    """A small real PNG with a single large digit on white (drawn with PIL)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (96, 96), (255, 255, 255))
    d = ImageDraw.Draw(img)
    # Draw the digit large and centred; the default bitmap font is enough to read.
    d.text((34, 28), PROBE_DIGIT, fill=(0, 0, 0))
    d.rectangle([2, 2, 93, 93], outline=(0, 0, 0), width=2)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _body(model: str, data_uri: str) -> dict:
    return {
        "model": model,
        "max_completion_tokens": 16,
        "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": f"What digit is shown in the image? {_INSTRUCTION}"},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]}],
    }


def probe(base_url: str, model: str, *, timeout_s: float = 60.0) -> tuple[bool, str]:
    """Send one real image to the served model. Returns (accepted, detail)."""
    body = _body(model, _data_uri(_small_image_png()))
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
    a = ap.parse_args(argv)

    if a.fake:
        png = _small_image_png()
        print(f"image probe [DRY-RUN]: drew a {len(png)}-byte PNG of {PROBE_DIGIT!r}; no call made")
        return 0
    if not a.base_url:
        print("image probe: --base-url is required for a live probe (or pass --fake)",
              file=sys.stderr)
        return 3
    ok, detail = probe(a.base_url, a.model, timeout_s=a.timeout_s)
    print(f"image probe [{'OK' if ok else 'FAIL'}]: {detail}", file=sys.stderr if not ok else sys.stdout)
    if not ok:
        print("  the served model did not accept the image. Start it with multimodal enabled: "
              "glimmer_serve.sh sets --limit-mm-per-prompt.image 1 (LIMIT_MM_IMAGE).",
              file=sys.stderr)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
