"""Run one SAM 3.1 call on a public rack photo and log it.

    META_API_KEY=... python scripts/sam_multirun.py PHOTO_DIR NAME PROMPT

PROMPT is a text concept ("server") or a raw visual prompt string
("<0f>1<|box;...|>"). The raw output goes to PHOTO_DIR/sam-raw/NAME.txt and
one line is appended to rack/sam-runs.jsonl. A hard cap on total calls is
read from that log, so a re-run can never go over budget.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from sam_rack import call_sam  # type: ignore[import-not-found]

from wattnot.sam.segment import repo_root

BUDGET = 15  # new calls allowed for this round
ROUND = "ai-rack"  # only log lines tagged with this round count
USD_PER_CALL = 0.03  # billed price per SAM 3.1 call


def main() -> int:
    directory, name, prompt = Path(sys.argv[1]).resolve(), sys.argv[2], sys.argv[3]
    log = repo_root() / "rack" / "sam-runs.jsonl"
    raw = log.read_text().splitlines() if log.exists() else []
    lines = [json.loads(x) for x in raw if x.strip()]
    used = sum(1 for e in lines if e.get("round") == ROUND)
    total = len(lines)
    if used >= BUDGET:
        print(f"budget spent ({used}/{BUDGET}), not calling", file=sys.stderr)
        return 3
    key = os.environ["META_API_KEY"]
    base = os.environ.get("META_BASE_URL", "https://api.meta.ai/v1")
    photo = directory / "rack.jpg"
    text, usage = call_sam(prompt, base64.b64encode(photo.read_bytes()).decode(), key, base)
    (directory / "sam-raw").mkdir(exist_ok=True)
    (directory / "sam-raw" / f"{name}.txt").write_text(text + "\n", encoding="utf-8")
    entry = {"n": total + 1, "round": ROUND, "at": datetime.now(UTC).isoformat(timespec="seconds"),
             "photo": str(directory.relative_to(repo_root())), "name": name,
             "prompt": prompt, "raw": f"sam-raw/{name}.txt", "usd_estimate": USD_PER_CALL,
             "usage": usage}
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    print(f"call {used + 1}/{BUDGET} ok, {text.count('<|box')} boxes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
