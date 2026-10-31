#!/usr/bin/env python3
"""Mocks 1031 / Task 2: live memory compression with the real SparkSummarizer.

Runs MemoryCompressionMiddleware -- the one in src/wattnot/context_saver/middleware.py, driven by
the LIVE Muse Spark summariser (SparkSummarizer) -- over the Oct-31 gap-test cases, as a real agent
loop (wattnot.agentwaste.bench.harness.run_case). Every model call has the middleware rewrite the
messages first, so this exercises the tool-call/tool-reply pairing fix (middleware.py lines ~184-194)
that an earlier gap run tripped over: that run logged 9/16 memcomp runs failing with HTTP 400
"orphaned tool_call_id". Here we count HTTP 400s explicitly and expect zero.

All spend goes through the guarded Meta ledger under a hard $0.50 job cap (purpose prefix
"mocks-1031-memcomp"); a call is refused before it is sent once the cap would be crossed. Tokens are
MEASURED from the API usage field (read back from the ledger). The summariser is memoised by text
hash so byte-identical turns are not re-summarised -- it only ever calls the real SparkSummarizer.

    PYTHONPATH=src python scripts/mocks1031/memcomp_live.py --reps 1

Writes data/gap-1031/context-saver/live-spark-1031.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from wattnot.agentwaste.bench.cases import all_cases  # noqa: E402
from wattnot.agentwaste.bench.harness import run_case  # noqa: E402
from wattnot.context_saver.middleware import (MemoryCompressionMiddleware,  # noqa: E402
                                              SparkSummarizer, conversation_tokens)
from wattnot.meta.client import GuardedClient  # noqa: E402
from wattnot.meta.ledger import SpendLedger  # noqa: E402

JOB_CAP_USD = 0.50          # hard cap; the job's own ledger spend under PREFIX may never cross it
PREFIX = "mocks-1031-memcomp"   # override with --prefix for a fresh cap budget
SUMM_SUBPURPOSE = "context_saver.memory_compression"
OUT = ROOT / "data" / "gap-1031" / "context-saver"
# The same eight cases the earlier gap run used, so the 0x400 result is comparable to RESULTS.md.
CASES = ["retry-1-status-500", "retry-2-write-503", "retry-3-docs-empty",
         "noprog-1-search-summarize", "noprog-2-ticket-poll", "noprog-3-file-poll",
         "normal-react-W1", "normal-stop-W1"]


def own_spend(ledger: SpendLedger, prefix: str) -> float:
    return sum(e.get("usd", 0) or 0 for e in ledger.entries()
               if str(e.get("purpose", "")).startswith(prefix))


class JobClient:
    """GuardedClient + this job's hard cap. Forces the job prefix onto every purpose (agent and
    summariser alike) and refuses before sending once the cap would be crossed. The cap is the
    cumulative ledger spend under `prefix`, so a fresh prefix gives a fresh budget."""

    def __init__(self, prefix: str = PREFIX, cap_usd: float = JOB_CAP_USD,
                 reserve_usd: float = 0.03) -> None:
        self.ledger = SpendLedger()
        self.client = GuardedClient(ledger=self.ledger)
        self.prefix, self.cap, self.reserve = prefix, cap_usd, reserve_usd

    def own(self) -> float:
        return own_spend(self.ledger, self.prefix)

    def __call__(self, body, *, purpose: str = None):
        purpose = purpose or self.prefix
        if not purpose.startswith(self.prefix):
            purpose = f"{self.prefix}:{purpose}"
        if self.own() + self.reserve > self.cap:
            raise RuntimeError(f"{self.prefix} job cap ${self.cap:.2f} reached")
        return self.client(body, purpose=purpose)


class MemoSummarizer:
    """Delegates to the real SparkSummarizer, memoised by text hash so byte-identical turns are not
    re-summarised. Exposes the SparkSummarizer's own .calls/.failures for honest reporting."""

    def __init__(self, job: JobClient) -> None:
        self.inner = SparkSummarizer(client=job)
        self.model = self.inner.model
        self.cache: dict[str, str] = {}
        self.lock = threading.Lock()

    @property
    def calls(self) -> int:
        return self.inner.calls

    @property
    def failures(self) -> int:
        return self.inner.failures

    def __call__(self, text: str) -> str:
        k = hashlib.sha256(text.encode()).hexdigest()
        with self.lock:
            if k in self.cache:
                return self.cache[k]
        out = self.inner(text)
        with self.lock:
            self.cache[k] = out
        return out


class SaverClient:
    """Applies MemoryCompressionMiddleware to body['messages'] before forwarding, and records the
    middleware's own before/after token accounting (ESTIMATE) across the run's calls."""

    def __init__(self, job: JobClient, summ: MemoSummarizer) -> None:
        self.job, self.summ = job, summ
        self.applies = 0
        self.tok_before = 0
        self.tok_after = 0
        self.est_before = 0
        self.est_after = 0

    def __call__(self, body, **kw):
        msgs = body.get("messages") or []
        new, st = MemoryCompressionMiddleware(summarize=self.summ).apply(list(msgs))
        if conversation_tokens(new) <= conversation_tokens(msgs):
            body = {**body, "messages": new}
        if st.get("applied"):
            self.applies += 1
            self.tok_before += st.get("tokens_before", 0)
            self.tok_after += st.get("tokens_after", 0)
        self.est_before += conversation_tokens(msgs)
        self.est_after += conversation_tokens(body["messages"])
        return self.job(body, **kw)


def classify(err: str | None) -> str:
    if not err:
        return "ok"
    if "400" in err:
        return "http_400"
    low = err.lower()
    if any(w in low for w in ("500", "502", "503", "504", "429", "timeout", "connection",
                              "network", "temporarily", "overload", "unavailable", "server error",
                              "urlerror", "url error", "reset", "refused", "eof")):
        return "transient_api"      # not a memcomp defect: upstream Meta API / network hiccup
    return "other"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--only", default="")
    ap.add_argument("--prefix", default=PREFIX, help="ledger purpose prefix (fresh prefix = fresh cap budget)")
    ap.add_argument("--cap", type=float, default=JOB_CAP_USD, help="hard job cap USD (<=0.50)")
    a = ap.parse_args()
    cap = min(a.cap, 0.50)      # never exceed the task's $0.50 ceiling
    OUT.mkdir(parents=True, exist_ok=True)
    job = JobClient(prefix=a.prefix, cap_usd=cap)
    spend0 = job.own()
    summ = MemoSummarizer(job)
    cases = {c.id: c for c in all_cases()}
    ids = [x for x in (a.only.split(",") if a.only else CASES) if x]

    rows = []
    capped = False
    for rep in range(a.reps):
        for cid in ids:
            sc = SaverClient(job, summ)
            try:
                r = run_case(cases[cid], sc, "harness", rep=rep,
                             purpose=f"{a.prefix}-{cid}").as_dict()
            except RuntimeError as e:  # job cap reached mid-run
                capped = True
                print(f"STOP ({cid} rep{rep}): {e}", flush=True)
                break
            r["arm"] = "harness+memcomp"
            r["memcomp_applies"] = sc.applies
            r["memcomp_tokens_before"] = sc.tok_before
            r["memcomp_tokens_after"] = sc.tok_after
            r["est_tokens_before"] = sc.est_before
            r["est_tokens_after"] = sc.est_after
            r["err_kind"] = classify(r.get("error"))
            r.pop("trace", None)
            rows.append(r)
            print(cid, f"rep{rep}", "ok" if r["success"] else "FAIL",
                  f"calls={r['calls']}", f"prompt={r['prompt_tokens']}",
                  f"applies={sc.applies}", f"err={r['err_kind']}", flush=True)
        if capped:
            break

    # persist full rows (incl. error strings) for honest diagnosis
    with (OUT / "live-spark-1031.rows.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    n = len(rows) or 1
    http_400 = sum(1 for r in rows if r["err_kind"] == "http_400")
    kinds = ("http_400", "transient_api", "other")
    errs = {k: sum(1 for r in rows if r["err_kind"] == k) for k in kinds}
    error_samples = [{"case": r["case"], "kind": r["err_kind"], "error": r.get("error")}
                     for r in rows if r.get("error")][:8]

    # MEASURED token/cost split from the ledger: summariser sub-purpose vs the agent calls.
    mine = [e for e in job.ledger.entries() if str(e.get("purpose", "")).startswith(a.prefix)]
    def toks(entries, key):
        return sum(e.get(key, 0) or 0 for e in entries)
    summ_entries = [e for e in mine if SUMM_SUBPURPOSE in str(e.get("purpose", ""))]
    agent_entries = [e for e in mine if SUMM_SUBPURPOSE not in str(e.get("purpose", ""))]

    applied = [r for r in rows if r["memcomp_applies"] > 0]
    mc_before = sum(r["memcomp_tokens_before"] for r in applied)
    mc_after = sum(r["memcomp_tokens_after"] for r in applied)

    res = {
        "spec": "wattnot.mocks-1031.memcomp-live/1",
        "generated": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "arm": "harness+memcomp",
        "summariser": "SparkSummarizer (live Muse Spark, memoised by text hash)",
        "model": summ.model,
        "cases": ids,
        "reps": a.reps,
        "runs": len(rows),
        "capped": capped,
        "pass_rate": {"passed": sum(r["success"] for r in rows), "runs": len(rows),
                      "pct": round(100 * sum(r["success"] for r in rows) / n, 1), "label": "MEASURED"},
        "http_errors": {"http_400": http_400, "breakdown": errs, "label": "MEASURED",
                        "samples": error_samples,
                        "note": "earlier gap run logged 9/16 memcomp runs with HTTP 400 orphaned "
                                "tool_call_id; expect 0 here after the pairing fix. transient_api "
                                "= upstream Meta API 5xx/overload, not a memcomp defect"},
        "memcomp_tokens": {"applied_runs": len(applied), "tokens_before": mc_before,
                           "tokens_after": mc_after,
                           "saved": mc_before - mc_after,
                           "saved_pct": round(100 * (mc_before - mc_after) / mc_before, 2) if mc_before else 0.0,
                           "label": "ESTIMATE (text-length token estimate, off-GPU)"},
        "prompt_tokens_per_run": round(toks(agent_entries, "input_tokens") / n, 1),
        "summariser_calls": summ.calls,
        "summariser_failures": summ.failures,
        "summariser_tokens": {"input": toks(summ_entries, "input_tokens"),
                              "output": toks(summ_entries, "output_tokens"),
                              "calls_ledgered": len(summ_entries), "label": "MEASURED"},
        "agent_tokens": {"input": toks(agent_entries, "input_tokens"),
                         "output": toks(agent_entries, "output_tokens"),
                         "calls_ledgered": len(agent_entries), "label": "MEASURED"},
        "spend_usd": round(job.own() - spend0, 6),
        "job_cap_usd": cap,
        "prefix": a.prefix,
        "spend_label": "MEASURED (Meta spend ledger)",
    }
    (OUT / "live-spark-1031.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps({k: res[k] for k in ("runs", "capped", "pass_rate", "http_errors",
                                          "memcomp_tokens", "summariser_calls", "summariser_failures",
                                          "spend_usd")}, indent=1))
    print("wrote", OUT / "live-spark-1031.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
