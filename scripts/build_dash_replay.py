"""Pack the real H100 results into the single JSON the dashboard replays.

    python scripts/build_dash_replay.py RESULTS_DIR [-o data/dash/h100-replay.json]

RESULTS_DIR holds the ``h100-*`` run folders. Nothing is read from the GPU and nothing is
invented: every sample is a row from sweep.csv, a trace csv or the adaptive_topk runs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from wattnot.dash import gpu


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("results", type=Path)
    p.add_argument("-o", "--out", type=Path, default=Path("data/dash/h100-replay.json"))
    a = p.parse_args()
    bundle = gpu.build_bundle(a.results)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(bundle, separators=(",", ":")))
    print(f"{a.out}: {len(bundle['scenes'])} scenes, {a.out.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
