"""Run the eye test once and write its result — used by the Glimmer test plan.

    python -m wattnot.vision.run --fake --out runs/t1 --budget-s 90

``--fake`` uses the offline answerer (ground truth, no network, not a
measurement): it exercises the plumbing inside the 2-hour Glimmer test without
a launch or a paid call. A live run passes ``--base-url`` to reach the served
vision model. ``--budget-s`` caps the wall-clock the eye test may take, so it
always fits the test window; images left unanswered by the cut count as misses.

Exit code 0 when every image reads correctly, 1 on any miss or error, so the
block driver sees a failing eye test as a failing block.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wattnot.vision.assets import load_eye_test
from wattnot.vision.client import (
    DEFAULT_VISION_MODEL,
    OfflineAnswerer,
    ServedVisionAnswerer,
    answer_eye_test,
)
from wattnot.vision.gate import vision_gate


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="WattNot vision quality gate (eye test)")
    ap.add_argument("--assets", default=None, help="eye-test asset dir (default: data/vision/eyetest)")
    ap.add_argument("--out", default=None, help="write eye_test.json here")
    ap.add_argument("--budget-s", type=float, default=None, help="wall-clock budget for the eye test")
    ap.add_argument("--fake", action="store_true", help="offline answerer (no network, not measured)")
    ap.add_argument("--base-url", default=None, help="served vision model base URL (live only)")
    ap.add_argument("--model", default=DEFAULT_VISION_MODEL)
    args = ap.parse_args(argv)

    eye = load_eye_test(args.assets, require_files=not args.fake)
    if args.fake or not args.base_url:
        answerer = OfflineAnswerer(eye)
        mode = "fake"
    else:
        answerer = ServedVisionAnswerer(eye.base_dir, model=args.model, base_url=args.base_url)
        mode = "live"

    answers, run_meta = answer_eye_test(eye, answerer, budget_s=args.budget_s)
    result = vision_gate(answers, eye.items, wh_per_image=eye.wh_per_image)

    summary = {
        "spec": "wattnot.eyetest/1",
        "mode": mode,
        "model": args.model,
        "label": "SYNTHETIC",
        "passed": result.passed,
        "reason": result.reason,
        "run": run_meta,
        "detail": result.detail,
    }
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "eye_test.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    miss = result.detail.get("missed") or []
    print(f"eye test [{mode}]: {result.reason}"
          + (f" (budget {args.budget_s}s hit)" if run_meta.get("budget_hit") else ""))
    for row in result.detail.get("images", []):
        if not row["ok"]:
            print(f"  MISS {row['id']} ({row['kind']}): expected {row['expected']!r} got {row['got']!r}")
    return 0 if result.passed and not miss else 1


if __name__ == "__main__":
    raise SystemExit(main())
