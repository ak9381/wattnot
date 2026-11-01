#!/usr/bin/env python3
"""Start any round the same way: one provider flag, the public round by default.

    PYTHONPATH=.:src python scripts/e2e/round.py --provider baseten --dry-run      # CPU, $0, MOCK
    PYTHONPATH=.:src python scripts/e2e/round.py --provider modal                  # prints the live command
    PYTHONPATH=.:src python scripts/e2e/round.py --provider combined --dry-run     # H100 + Baseten + Modal, then routing
    PYTHONPATH=.:src python scripts/e2e/round.py --provider h100 --plan            # profile, caps, estimate

Without --dry-run this script only PRINTS the live commands; it never launches a paid run. A live
round is started by running the printed command by hand, after Akash's go, inside each runner's
own guards (WATTNOT_MODAL_GO, the $8 Baseten hard stop, ctl.py's VM lifetime and Rs cap).
See docs/PROVIDERS.md.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from wattnot import providers as P  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--provider", required=True, choices=["modal", "baseten", "h100", "combined"])
    ap.add_argument("--dry-run", action="store_true", help="run the CPU dry run (MOCK numbers, $0)")
    ap.add_argument("--smoke", action="store_true", help="live: the provider's smoke size")
    ap.add_argument("--plan", action="store_true", help="print profile, public round and ESTIMATE only")
    ap.add_argument("--out", default="/tmp/wattnot-dry")
    a = ap.parse_args(argv)
    prof = P.PROFILES[a.provider]
    info = {"provider": a.provider, "profile": prof, "public_round": P.public_round(a.provider),
            "estimate": P.estimate(a.provider)}
    cmds = P.commands(a.provider, dry_run=a.dry_run, smoke=a.smoke, out=a.out)
    if a.plan or not a.dry_run:
        info["commands"] = [shlex.join(c) for c in cmds]
        if not a.dry_run:
            from wattnot.launch_check import run as preflight
            pf = preflight(a.provider, dash=False)
            info["preflight"] = {"ok": pf["ok"], "failed": [c for leg in (pf.get("legs") or {a.provider: pf}).values()
                                                         for c in leg["checks"] if not c["ok"]]}
            info["preflight_cmd"] = f"python scripts/e2e/preflight.py --provider {a.provider}"
            info["note"] = ("NOT RUN: live commands need Akash's go; run them by hand"
                            + ("" if pf["ok"] else ". PREFLIGHT FAILED: fix before any paid launch"))
        print(json.dumps(info, indent=1))
        return 0
    env = {**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'src'}"}
    for k in ("BASETEN_METRICS_KEY", "BASETEN_API_KEY", "WATTNOT_MODAL_GO"):
        env.pop(k, None)   # a dry run can never reach a paid endpoint
    rcs = []
    for c in cmds:
        c = [sys.executable if x == "python" else x for x in c]
        print(f"[dry-run] {shlex.join(c)}", flush=True)
        rcs.append(subprocess.run(c, cwd=ROOT, env=env).returncode)
    if a.provider == "combined":
        rid = P.PUBLIC_ROUND["combined"]
        h = P.load_json(Path(a.out) / "h100-combined" / "runs" / f"m-public-{rid}" / f"{rid}.json")
        b = P.load_json("/tmp/wattnot-dry/baseten/r02.json")
        m = P.load_json(ROOT / "data" / "modal-full-1101" / "fake" / P.PUBLIC_ROUND["modal"] / "inner.json")
        dec = P.route(P.combine(b, m, h))
        dec["label"] = "MOCK"
        (Path(a.out) / "combined-route.json").write_text(json.dumps(dec, indent=1))
        print(json.dumps({"route": {k: v.get("route_to") for k, v in dec["decision"].items()},
                          "legs_found": {"h100": bool(h), "baseten": bool(b), "modal": bool(m)}}))
        rcs.append(0 if h and b and m else 1)
    print(json.dumps({"provider": a.provider, "rcs": rcs, "ok": all(r == 0 for r in rcs)}))
    return 0 if all(r == 0 for r in rcs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
