#!/usr/bin/env python3
"""Second-layer benchmark: the saver behind three harness loops, on real Muse Spark calls.

    python scripts/agentwaste_v2_bench.py --reps 2 --max-spend 3.0

Writes data/agent-waste/v2/runs.jsonl (one line per finished run, appended as runs end so a
crash loses nothing) and results.json (read by docs and by the dashboard card).

Spend rules for this job: the ledger ceiling here is JOB_CEILING (the shared ledger's own code
cap is lower; this job raises it only for itself, in this script), and --max-spend limits what
this benchmark itself spends (every ledger entry whose purpose starts with `agentwaste-v2`,
pilot runs included).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wattnot.agentwaste.bench.cases import all_cases  # noqa: E402
from wattnot.agentwaste.bench.harness import (  # noqa: E402
    HELPER_CAP,
    PLANNER_CAP,
    REACT_CAP,
    SHARED_CALLS,
    STOP_CAP,
    run_case,
)
from wattnot.agentwaste.bench.hsummary import ARMS, summarize  # noqa: E402
from wattnot.agentwaste.config import WasteConfig  # noqa: E402
from wattnot.meta.client import GuardedClient  # noqa: E402
from wattnot.meta.ledger import SpendLedger  # noqa: E402

JOB_CEILING = 24.00      # the user's ceiling for this job; their overall hard ceiling is 39.50
PURPOSE = "agentwaste-v2"


class SpendLimited:
    """Refuses new calls once this benchmark's own spend, or the ledger total, is over."""

    def __init__(self, client: GuardedClient, ledger: SpendLedger, max_spend: float) -> None:
        self.client, self.ledger, self.max_spend = client, ledger, max_spend
        self.lock = threading.Lock()

    def own(self) -> float:
        return sum(e["usd"] for e in self.ledger.entries()
                   if str(e.get("purpose", "")).startswith(PURPOSE))

    def __call__(self, body, **kw):
        with self.lock:
            if self.own() >= self.max_spend or self.ledger.total() >= JOB_CEILING - 0.25:
                raise RuntimeError("spend limit reached")
        return self.client(body, **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--effort", default="low")
    ap.add_argument("--max-spend", type=float, default=3.0)
    ap.add_argument("--only", default="", help="comma list of case ids or prefixes")
    ap.add_argument("--resume", action="store_true", help="skip runs already in runs.jsonl")
    ap.add_argument("--summarize-only", action="store_true", help="no API calls")
    ap.add_argument("--out", default=str(ROOT / "data" / "agent-waste" / "v2"))
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    runs_path = out / "runs.jsonl"
    ledger = SpendLedger(cap_usd=JOB_CEILING, soft_usd=JOB_CEILING - 1)
    client = SpendLimited(GuardedClient(ledger=ledger), ledger, a.max_spend)
    ledger_before = ledger.total()
    t0 = datetime.now(UTC)

    rows: list[dict] = []
    if runs_path.exists() and (a.resume or a.summarize_only):
        rows = [json.loads(x) for x in runs_path.read_text().splitlines() if x.strip()]
    if not a.summarize_only:
        wanted = [x for x in a.only.split(",") if x]
        cases = [c for c in all_cases() if not wanted or any(c.id.startswith(w) for w in wanted)]
        done = {(r["case"], r["arm"], r["rep"]) for r in rows if not r["error"]}
        rows = [r for r in rows if not r["error"]]
        jobs = [(c, arm, rep) for rep in range(a.reps) for c in cases for arm in ARMS
                if (c.id, arm, rep) not in done]
        random.Random(11).shuffle(jobs)   # no arm gets a quieter API
        lock = threading.Lock()

        def one(j):
            c, arm, rep = j
            res = run_case(c, client, arm, effort=a.effort, rep=rep).as_dict()
            with lock:
                with runs_path.open("a") as f:
                    f.write(json.dumps(res) + "\n")
            return res

        with ThreadPoolExecutor(a.workers) as ex:
            rows += list(ex.map(one, jobs))
        order = {c.id: i for i, c in enumerate(all_cases())}
        rows.sort(key=lambda r: (order.get(r["case"], 99), r["rep"], r["arm"]))
        runs_path.write_text("".join(json.dumps(r) + "\n" for r in rows))

    summary = summarize(rows)
    own = client.own()
    summary.update({
        "generated": t0.strftime("%Y-%m-%dT%H:%M:%SZ"), "reps": a.reps, "effort": a.effort,
        "caps": {"react_max_iterations": REACT_CAP, "stop_max_rounds": STOP_CAP,
                 "planner_max_iterations": PLANNER_CAP, "helper_max_iterations": HELPER_CAP,
                 "saver_shared_calls": SHARED_CALLS},
        "waste_config": WasteConfig.second_layer().__dict__,
        "cases_total": len({r["case"] for r in rows}), "runs": len(rows),
        "errors": sum(1 for r in rows if r["error"]),
        "spend_usd_own": round(own, 4), "spend_label": "MEASURED (Meta spend ledger)",
        "ledger_total_after": round(ledger.total(), 4),
        "ledger_total_before": round(ledger_before, 4),
        "job_ceiling": JOB_CEILING,
    })
    (out / "results.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({"normal": summary["normal"]["saver_vs_harness"],
                      "injected": summary["injected"]["saver_vs_harness"],
                      "spend_usd_own": summary["spend_usd_own"],
                      "ledger_total_after": summary["ledger_total_after"],
                      "errors": summary["errors"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
