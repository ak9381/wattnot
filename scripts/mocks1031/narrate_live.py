#!/usr/bin/env python3
"""Mocks 1031: ONE live Muse Spark narration pass for the recorded dashboard replay.

This is the only script that spends money for the dashboard narrator. It builds the recorded
run's frames with runplan.plan() (a dry REPLAY -- no GPU, no network to construct), then calls
wattnot.dash.narrator.generate_and_cache through a GuardedClient wrapped in a hard $0.30 job cap.
Every call is written to the spend ledger before it is sent, and this job refuses a call once its
own ledger entries (purpose prefix "mocks-1031-narrate") plus a small reserve would cross the cap,
so this pass can never spend more than $0.30.

The two narrated steps (room pick, sweep pick) cost a few hundred tokens total, well under the cap.
The result is cached to data/narration-1031/<run_id>.json; the dashboard reads it and labels those
lines LIVE with the model id and the reply's trace id. Run once:

    PYTHONPATH=src python scripts/mocks1031/narrate_live.py --model glimmer

Nothing else in the repo calls the live model for narration; offline, the rule-based template line
stays and is labelled "template".
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from wattnot.dash import narrator, runplan  # noqa: E402
from wattnot.meta.client import GuardedClient  # noqa: E402
from wattnot.meta.ledger import SpendLedger  # noqa: E402

JOB_CAP_USD = 0.30
PREFIX = "mocks-1031-narrate"
STORY = ROOT / "data" / "dash" / "story.json"
NARR_MODEL = "muse-spark-1.3"


def own_spend(ledger: SpendLedger) -> float:
    return sum(e.get("usd", 0) or 0 for e in ledger.entries()
               if str(e.get("purpose", "")).startswith(PREFIX))


class JobClient:
    """GuardedClient + this job's $0.30 cap. Refuses before sending if the cap would be crossed."""

    def __init__(self, reserve_usd: float = 0.02) -> None:
        self.ledger = SpendLedger()
        self.client = GuardedClient(ledger=self.ledger)
        self.reserve = reserve_usd

    def own(self) -> float:
        return own_spend(self.ledger)

    def __call__(self, body, *, purpose: str = PREFIX):
        if not purpose.startswith(PREFIX):
            purpose = f"{PREFIX}:{purpose}"
        if self.own() + self.reserve > JOB_CAP_USD:
            raise RuntimeError(f"{PREFIX} job cap ${JOB_CAP_USD:.2f} reached")
        return self.client(body, purpose=purpose)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="glimmer", help="recorded run model id (default: glimmer)")
    ap.add_argument("--model-name", default=NARR_MODEL, help="Muse Spark model id to call")
    ap.add_argument("--root", default=None, help="cache root (default: data/narration-1031)")
    a = ap.parse_args()

    if not STORY.is_file():
        print(f"story bundle not found: {STORY} (build it with scripts/build_dash_arch.py)")
        return 2
    bundle = json.loads(STORY.read_text())

    # Build the recorded run's frames with NO narration overlay (dry REPLAY, no network).
    p = runplan.plan(bundle, {"model": a.model})
    frames, run_id = p["events"], p["run_id"]
    targets = narrator.narrated_step_ids(frames)
    cache_path = narrator.cache_path_for(run_id, a.root)
    print(f"run_id={run_id}  steps to narrate={targets}  cache={cache_path}")

    job = JobClient()
    spend0 = job.own()
    narr = narrator.generate_and_cache(frames, cache_path, client=job, model=a.model_name)

    summ = job.ledger.summary()
    mine = [e for e in job.ledger.entries() if str(e.get("purpose", "")).startswith(PREFIX)]
    spent = round(job.own() - spend0, 6)
    print(json.dumps({
        "run_id": run_id,
        "cache_path": str(cache_path),
        "narrated_steps": list(narr),
        "calls": len(mine),
        "input_tokens": sum(e.get("input_tokens", 0) for e in mine),
        "output_tokens": sum(e.get("output_tokens", 0) for e in mine),
        "spend_delta_usd": spent,
        "job_cap_usd": JOB_CAP_USD,
        "ledger_total_usd": summ["total_usd"],
        "model": a.model_name,
        "label": "MEASURED (Meta spend ledger)",
    }, indent=1))
    for sid, n in narr.items():
        print(f"  {sid}: [{n['label']}] ({n['model']} / {n['trace_id']}) {n['text']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
