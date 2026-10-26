"""Segment a rack photo with SAM 3.1 on the Meta Model API.

    META_API_KEY=... python scripts/sam_rack.py [--dir rack/real]

Sends the photo in --dir (public, license-clear, nothing private) in two
calls: the concept prompt "server" finds the slots on its own, then one
positive point each on the rack frame and the power panel asks SAM for
those two objects. The points only say where to look; the outline SAM
returns is compared against our hand-drawn layout. Raw output is saved to
<dir>/sam-raw/ so tests replay it offline. Writes <dir>/masks.sam.json and
<dir>/sam.spend.json. On a failed call nothing is overwritten, and the rack
view keeps the manual layout.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from wattnot.sam.sam_parse import build_document, parse_by_object
from wattnot.sam.segment import image_size, load_masks, repo_root, validate_masks

MAX_CALLS = 2
# Positive points (x, y) on the 960x720 photo: rack frame and wall power panel.
POINTS = {1: ("rack", (455, 400)), 2: ("pdu", (940, 508))}


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


def point_prompt(w: int, h: int) -> str:
    objs = [f"{oid}<|point;x={x};y={y};w={w};h={h}|>" for oid, (_, (x, y)) in POINTS.items()]
    return "<0f>" + ",".join(objs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="rack/real")
    args = ap.parse_args()
    key = os.environ.get("META_API_KEY")
    if not key:
        print("META_API_KEY is not set", file=sys.stderr)
        return 2
    base = os.environ.get("META_BASE_URL", "https://api.meta.ai/v1")
    rack = repo_root() / args.dir if not Path(args.dir).is_absolute() else Path(args.dir)
    photo = rack / "rack.jpg"
    w, h = image_size(photo)
    image_b64 = base64.b64encode(photo.read_bytes()).decode()
    calls = {"server": "server", "points": point_prompt(w, h)}
    raw, usage = {}, {}
    try:
        for name, prompt in list(calls.items())[:MAX_CALLS]:
            raw[name], usage[name] = call_sam(prompt, image_b64, key, base)
    except Exception as exc:
        print(f"SAM call failed, keeping the manual layout: {exc}", file=sys.stderr)
        return 1
    (rack / "sam-raw").mkdir(exist_ok=True)
    for name, text in raw.items():
        (rack / "sam-raw" / f"{name}.txt").write_text(text + "\n", encoding="utf-8")
    doc = build_document(split_by_kind(raw), load_masks(rack / "masks.manual.json"))
    try:
        validate_masks(doc, image_path=photo)
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


def split_by_kind(raw: dict[str, str]) -> dict[str, str]:
    """Concept output feeds the slots; point output is split by object id."""

    by_kind = {"slot": raw.get("server", "")}
    records = parse_by_object(raw.get("points", ""))
    for oid, (kind, _) in POINTS.items():
        by_kind[kind] = records.get(oid, "")
    return by_kind


if __name__ == "__main__":
    raise SystemExit(main())
