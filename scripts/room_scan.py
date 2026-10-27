#!/usr/bin/env python3
"""Room scan: rack masks from SAM, a thermal frame, a power cross-check, a decision per rack.

  python scripts/room_scan.py --room arsat                 # replays saved SAM output, no spend
  python scripts/room_scan.py --room arsat --live          # segments now (about $0.0025)
  python scripts/room_scan.py --room arsat --thermal ir.jpg --range 20 41.5   # real IR still
  python scripts/room_scan.py --ir-read rack/room/irpair/thermal.jpg --range 20 41.5

Prints the room scan JSON (schema: docs/SAM-ROLE.md) and writes it, with the photo and the
thermal frames, to --out (default web/room/data/<room>). Labels travel in the JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wattnot.sam import room  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--room", default="arsat")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--thermal", type=Path, help="a real IR still of the same view")
    ap.add_argument("--range", nargs=2, type=float, metavar=("LO_C", "HI_C"))
    ap.add_argument("--live", action="store_true", help="call SAM 3.1 instead of replaying")
    ap.add_argument("--ir-read", type=Path, help="read an IR still with no masks and exit")
    ap.add_argument("--events", type=Path, metavar="ROOT",
                    help="also write the room_scan events to ROOT/runs/<run-id>/events.jsonl")
    ap.add_argument("--run-id", help="run id for --events (run_YYYYMMDD_HHMMSS_xxxxxx)")
    ap.add_argument("--quiet", action="store_true", help="write files, do not print the JSON")
    a = ap.parse_args(argv)
    if a.ir_read:
        if not a.range:
            ap.error("--ir-read needs --range LO_C HI_C")
        print(json.dumps(room.read_ir_still(a.ir_read, *a.range), indent=1))
        return 0
    client = None
    if a.live:
        from wattnot.sam.client import SamClient
        client = SamClient()
    out = a.out or ROOT / "web" / "room" / "data" / a.room
    rng = tuple(a.range) if a.range else None
    scan = room.build_scan(a.room, thermal=a.thermal, value_range=rng, live_sam=client, out=out)
    if a.events:
        from wattnot.events import Emitter
        from wattnot.events.emitter import new_run_id
        with Emitter(a.run_id or new_run_id(), root=a.events) as em:
            room.emit_events(scan, em)
            print(f"events: {em.path}", file=sys.stderr)
    if not a.quiet:
        print(json.dumps(room.public(scan), separators=(",", ":")))
    print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
