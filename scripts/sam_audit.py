"""Audit a rack photo, then say what the audit lets WattNot do.

    python scripts/sam_audit.py                         # replay saved SAM output, free
    python scripts/sam_audit.py --live rack/mine        # 3 SAM calls on rack/mine/rack.jpg

--live needs a Meta key (META_API_KEY or the login file) and goes through the spend ledger
(about $0.0075 for the three calls). The raw output is saved to <dir>/sam-raw/ so the next run
replays it. --fleet points at your inventory (default rack/audit/fleet.json).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wattnot.sam import physical as ph

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fleet", default=str(ROOT / "rack" / "audit" / "fleet.json"))
    ap.add_argument("--live", metavar="DIR",
                    help="send DIR/rack.jpg to SAM (3 calls, ledger-guarded)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.live:
        from wattnot.meta.client import load_key
        from wattnot.sam.client import SamClient
        if not load_key():
            print("no Meta key found: set META_API_KEY or sign in; nothing was sent",
                  file=sys.stderr)
            return 2
        scan = ph.scan_live(a.live, SamClient())
        print(f"{a.live}: SAM saw {len(scan.units)} units {scan.slots}")
    specs, _ = ph.load_fleet(a.fleet)
    on = ph.audit_fleet(specs, sam=True)
    if a.json:
        print(json.dumps(on.to_dict(), indent=2))
        return 0
    for r in on.racks:
        print(f"{r.spec.id}: {r.present_nodes} units in the photo "
              f"({len(r.spec.declared)} in the inventory), ceiling {r.ceiling_w} W per node")
        if r.unlisted:
            print(f"   units the inventory does not list: slots {list(r.unlisted)}")
        if r.unverified:
            print(f"   GPUs with no unit in the photo (will not be touched): {list(r.unverified)}")
    print("eligible GPUs:", on.eligible_gpus())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
