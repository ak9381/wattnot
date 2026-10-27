#!/usr/bin/env python3
"""Run the agent-waste benchmark against Muse Spark and write the results folder.

    python scripts/agentwaste_bench.py --reps 2 --max-spend 3.0

Writes data/agent-waste/runs.jsonl (one line per run) and results.json (the summary the docs and
the dashboard card read). Every call goes through the spend guard. The run stops, and says so,
when its own spend passes --max-spend or the ledger passes the hard stop.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wattnot.agentwaste.bench.runner import MAX_STEPS, run_all  # noqa: E402
from wattnot.agentwaste.bench.summary import summarize  # noqa: E402
from wattnot.agentwaste.bench.tasks import NOTHING, TASKS, _n  # noqa: E402
from wattnot.agentwaste.config import WasteConfig  # noqa: E402
from wattnot.meta.client import GuardedClient  # noqa: E402
from wattnot.meta.ledger import SpendLedger  # noqa: E402

HARD_STOP = 39.50
PURPOSE = "agentwaste-bench"


class SpendLimited:
    """Refuses new calls once this run's spend, or the ledger total, is over its limit."""

    def __init__(self, client: GuardedClient, ledger: SpendLedger, max_spend: float) -> None:
        self.client, self.ledger, self.max_spend = client, ledger, max_spend
        self.start = self._own()
        self.lock = threading.Lock()

    def _own(self) -> float:
        """Spend booked under this benchmark's purpose only, so other jobs sharing the ledger
        do not eat the benchmark's budget."""
        return sum(e["usd"] for e in self.ledger.entries()
                   if str(e.get("purpose", "")).startswith(PURPOSE))

    def spent(self) -> float:
        return self._own() - self.start

    def __call__(self, body, **kw):
        with self.lock:
            if self.spent() >= self.max_spend or self.ledger.total() >= HARD_STOP:
                raise RuntimeError("spend limit reached")
        return self.client(body, **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--effort", default="low")
    ap.add_argument("--max-spend", type=float, default=3.0)
    ap.add_argument("--retry-errors", action="store_true",
                    help="keep finished runs in runs.jsonl and redo only the ones that errored")
    ap.add_argument("--rescore", action="store_true",
                    help="re-judge the no-change checks from the saved replies; makes no API calls")
    ap.add_argument("--out", default=str(ROOT / "data" / "agent-waste"))
    a = ap.parse_args()

    ledger = SpendLedger(cap_usd=HARD_STOP, soft_usd=HARD_STOP - 1)
    client = SpendLimited(GuardedClient(ledger=ledger), ledger, a.max_spend)
    t0 = datetime.now(UTC)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.rescore:
        rows = [json.loads(x) for x in (out / "runs.jsonl").read_text().splitlines()]
        by_id = {t.id: t for t in TASKS}
        for r in rows:
            if by_id[r["task"]].check is _n and not r["error"]:
                r["success"] = r["mutations"] == 0 and bool(
                    NOTHING.search("\n".join(r["said"]).lower()))
        prev = json.loads((out / "results.json").read_text()).get("spend_usd_ledger", 0.0)
        spent = prev
    elif a.retry_errors:
        rows = [json.loads(x) for x in (out / "runs.jsonl").read_text().splitlines()]
        bad = {(r["task"], r["arm"], r["rep"]) for r in rows if r["error"]}
        by_id = {t.id: t for t in TASKS}
        for task_id, arm, rep in sorted(bad):
            (new,) = run_all(client, arms=[arm], tasks=[by_id[task_id]], reps=1,
                             workers=1, effort=a.effort)
            d = new.as_dict()
            d["rep"] = rep
            rows = [d if (r["task"], r["arm"], r["rep"]) == (task_id, arm, rep) else r
                    for r in rows]
        prev = client.start  # all earlier benchmark spend, pilot runs included
    else:
        rows = [r.as_dict() for r in run_all(client, reps=a.reps, workers=a.workers,
                                             effort=a.effort)]
        prev = 0.0  # the first full run starts after the pilot; its own delta is reported
    if not a.rescore:
        spent = client.spent() + prev
    (out / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    summary = summarize(rows)
    summary.update({
        "schema": "wattnot.agent-waste/1",
        "generated": t0.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tasks": len(TASKS), "reps": a.reps, "arms": summary["arms"], "effort": a.effort,
        "max_steps": MAX_STEPS, "waste_config": WasteConfig().__dict__,
        "workers": a.workers,
        "spend_usd_ledger": round(spent, 4), "spend_label": "MEASURED (Meta spend ledger)",
        "errors": sum(1 for r in rows if r["error"]),
    })
    (out / "results.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: summary[k] for k in ("on_vs_off", "on_vs_auto", "spend_usd_ledger",
                                              "errors", "outcome_differs_on_vs_off")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
