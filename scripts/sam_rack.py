"""Segment the rack photo with SAM 3.1 on the Meta Model API.

    META_API_KEY=... python scripts/sam_rack.py

Sends rack/rack.jpg (our own drawing, nothing private) with one short
prompt per object kind: 3 calls in total. Raw output is saved to
rack/sam-raw/<kind>.txt so tests replay it without the network. Writes
rack/masks.sam.json and rack/sam.spend.json. If a call fails, nothing is
overwritten and the rack view keeps using the manual overlay.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from datetime import UTC, datetime

from wattnot.sam.sam_parse import build_document
from wattnot.sam.segment import load_manual_overlay, repo_root, validate_masks

PROMPTS = {"rack": "server rack", "slot": "GPU module", "pdu": "power strip"}
MAX_CALLS = 3


def call_sam(prompt: str, image_b64: str, key: str, base: str) -> tuple[str, dict]:
    body = {"model": "sam-3.1", "input": [{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": prompt},
        {"type": "input_image", "image_url": f"data:image/jpeg;base64,{image_b64}"},
    ]}]}
    req = urllib.request.Request(f"{base}/responses", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:  # noqa: S310
        data = json.load(resp)
    text = data.get("output_text") or "".join(
        part.get("text", "") for item in data.get("output", [])
        for part in item.get("content", []) or [] if isinstance(part, dict))
    return text, data.get("usage") or {}


def main() -> int:
    key = os.environ.get("META_API_KEY")
    if not key:
        print("META_API_KEY is not set", file=sys.stderr)
        return 2
    base = os.environ.get("META_BASE_URL", "https://api.meta.ai/v1")
    rack = repo_root() / "rack"
    image_b64 = base64.b64encode((rack / "rack.jpg").read_bytes()).decode()
    raw, usage = {}, {}
    try:
        for kind, prompt in list(PROMPTS.items())[:MAX_CALLS]:
            raw[kind], usage[kind] = call_sam(prompt, image_b64, key, base)
    except Exception as exc:  # keep the manual overlay on any failure
        print(f"SAM call failed, keeping the manual overlay: {exc}", file=sys.stderr)
        return 1
    (rack / "sam-raw").mkdir(exist_ok=True)
    for kind, text in raw.items():
        (rack / "sam-raw" / f"{kind}.txt").write_text(text + "\n", encoding="utf-8")
    doc = build_document(raw, load_manual_overlay())
    try:
        validate_masks(doc, image_path=rack / "rack.jpg")
    except ValueError as exc:
        print(f"SAM masks failed checks ({exc}); marking cut", file=sys.stderr)
        doc["status"] = "cut"
    (rack / "masks.sam.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    spend = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
             "model": "sam-3.1", "calls": len(raw), "images": len(raw),
             "usd_estimate": round(len(raw) * 2.5 / 1000, 4), "usage": usage}
    (rack / "sam.spend.json").write_text(json.dumps(spend, indent=2) + "\n", encoding="utf-8")
    print(f"status={doc['status']} iou_vs_manual={doc['iou_vs_manual']} calls={len(raw)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
