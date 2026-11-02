#!/usr/bin/env python3
"""$0 launch check before any paid round: python scripts/e2e/preflight.py --provider baseten|h100|modal|combined

Runs every Modal-failure lesson (see docs/PROVIDERS.md, "Launch checklist") locally. No network, no spend.
Exit 0 = launch allowed, 1 = fix first.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from wattnot.launch_check import run  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", required=True, choices=["baseten", "h100", "modal", "combined"])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-dash", action="store_true", help="skip starting the local dashboard")
    a = ap.parse_args(argv)
    rep = run(a.provider, dash=not a.no_dash)
    if a.json:
        print(json.dumps(rep, indent=1))
    else:
        legs = rep.get("legs") or {a.provider: rep}
        for name, leg in legs.items():
            for c in leg["checks"]:
                print(f"[{'ok' if c['ok'] else 'FAIL'}] {name:8s} {c['id']:16s} {c['detail']}")
        print(f"preflight {a.provider}: {'PASS' if rep['ok'] else 'FAIL'} ($0, no network)")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
