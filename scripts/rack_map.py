"""Draw the rack map for one run.

    python scripts/rack_map.py --trace traces/<run>/<label>.csv --out rack/rackmap.svg
    python scripts/rack_map.py --watts 0=312 2=287 --out rack/rackmap.svg

GPUs found in the trace (or given with --watts) are the ones the run
touched. Uses the first photo set present (rack/ai/, rack/cisco/, then rack/real/),
and its masks.sam.json
when that exists and passed its checks,
and the manual overlay otherwise.
"""

from __future__ import annotations

import argparse

from wattnot.sam import gpu_watts_from_trace, render_svg
from wattnot.sam.pick import image_note, photo_dir, pick_masks


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace")
    ap.add_argument("--watts", nargs="*", default=[])
    ap.add_argument("--out", default="rack/rackmap.svg")
    args = ap.parse_args()
    watts = gpu_watts_from_trace(args.trace) if args.trace else {}
    for pair in args.watts:
        gpu, w = pair.split("=")
        watts[int(gpu)] = float(w)
    doc, fallback = pick_masks()
    out = render_svg(doc, photo_dir() / doc["image"], watts, watts, args.out,
                     fallback=fallback, note=image_note(photo_dir()))
    print(f"{out} ({doc['source']}{', fallback' if fallback else ''})")


if __name__ == "__main__":
    main()
