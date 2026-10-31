#!/usr/bin/env python3
"""Turn an orchestrate sweep (vm/results.json) into the trials list the Wonder chart reads.

    python scripts/sweep_to_trials.py RUN_DIR [--out RUN_DIR/sweep_trials.json]

Each trial is one cap step: joules per correct answer = mean J/request at the cap divided by the
cap arm's quality pass rate. The label is copied from the step (MEASURED on a live run), so a
rehearsal sweep stays MOCK/SIMULATED. No API calls, no GPU.
"""
import argparse
import json
import sys
from pathlib import Path


def trials_from(results: dict) -> list[dict]:
    out = []
    for s in results.get("steps") or []:
        j = s.get("j_per_req_cap") or []
        q = ((s.get("quality") or {}).get("cap_pct"))
        if not j or q is None:
            continue
        if q == 0:   # the chart cannot plot an unbounded J/correct (it would draw at 0 = best)
            print(f"[trials] cap {s['cap_w']} W: 0% graded pass rate, J/correct unbounded; "
                  "left off the chart, see the sweep receipt", file=sys.stderr)
            continue
        mean_j = sum(j) / len(j)
        rc = s.get("reasoning") or {}
        out.append({"cap_w": s["cap_w"], "clock_profile": s.get("clock_profile") or "base",
                    "joules_per_correct": round(mean_j / (q / 100.0), 6),
                    "quality": round(q / 100.0, 4), "kept": bool(s.get("accepted")),
                    # reasoning tokens / completion tokens at this cap (item E); label MEASURED when
                    # from server usage, ESTIMATE when sized from the reasoning text
                    "reasoning_token_share": rc.get("share"),
                    "reasoning_token_label": rc.get("label"),
                    "label": s.get("label", "MEASURED")})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    src = a.run_dir / "vm" / "results.json"
    if not src.exists():
        print(f"no sweep results at {src}", file=sys.stderr)
        return 1
    trials = trials_from(json.loads(src.read_text()))
    if not trials:
        print("sweep has no cap steps with quality", file=sys.stderr)
        return 1
    out = a.out or a.run_dir / "sweep_trials.json"
    out.write_text(json.dumps({"trials": trials}, indent=2) + "\n")
    print(f"{len(trials)} trials -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
