#!/usr/bin/env python3
"""Gap tests 1 and 2: memory compression and tool-output dedup on real Muse Spark agent runs.

Arms (same cases, same harness, same model, temperature 0):
  harness          - no context saver
  harness+dedup    - ToolDedupMiddleware rewrites the messages before every model call
  harness+memcomp  - MemoryCompressionMiddleware with a LIVE Muse Spark summariser
                     (no Llama 8B on api.meta.ai, so the summariser is muse-spark-1.3)

Tokens are MEASURED from the API usage field (summariser calls counted separately).
Success is the case's own world-state check. Nothing mocked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from common import OUT, JobClient

from wattnot.agentwaste.bench.cases import all_cases
from wattnot.agentwaste.bench.harness import run_case
from wattnot.context_saver.middleware import (MemoryCompressionMiddleware, ToolDedupMiddleware,
                                              conversation_tokens)

SUMM_MODEL = "muse-spark-1.3"
CASES = ["retry-1-status-500", "retry-2-write-503", "retry-3-docs-empty",
         "noprog-1-search-summarize", "noprog-2-ticket-poll", "noprog-3-file-poll",
         "normal-react-W1", "normal-stop-W1"]
ARMS = ["harness", "harness+dedup", "harness+memcomp"]


class LiveSummarizer:
    model = SUMM_MODEL

    def __init__(self, job: JobClient) -> None:
        self.job, self.cache, self.calls, self.lock = job, {}, 0, threading.Lock()
        self.prompt_tokens = self.completion_tokens = 0

    def __call__(self, text: str) -> str:
        k = hashlib.sha256(text.encode()).hexdigest()
        with self.lock:
            if k in self.cache:
                return self.cache[k]
        r = self.job({"model": SUMM_MODEL, "reasoning_effort": "low", "max_tokens": 1500,
                      "temperature": 0, "messages": [
                          {"role": "system", "content": "Summarise this agent-conversation turn "
                           "in at most 2 short sentences. Keep ids, numbers, errors and file names."},
                          {"role": "user", "content": text[:6000]}]},
                     purpose="gap-1031-memcomp-summ")
        out = (r["choices"][0]["message"].get("content") or "").strip() or text[:160]
        u = r.get("usage") or {}
        with self.lock:
            self.cache[k] = out
            self.calls += 1
            self.prompt_tokens += u.get("prompt_tokens", 0)
            self.completion_tokens += u.get("completion_tokens", 0)
        return out


class SaverClient:
    """Applies one middleware to body['messages'] before forwarding. Counts the estimated cut."""

    def __init__(self, job, make_mw):
        self.job, self.make_mw = job, make_mw
        self.est_before = self.est_after = 0

    def __call__(self, body, **kw):
        msgs = body.get("messages") or []
        new, st = self.make_mw().apply(list(msgs))
        if conversation_tokens(new) <= conversation_tokens(msgs):
            body = {**body, "messages": new}
        self.est_before += conversation_tokens(msgs)
        self.est_after += conversation_tokens(body["messages"])
        return self.job(body, **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--only", default="")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    out = OUT / "context-saver"
    out.mkdir(parents=True, exist_ok=True)
    job = JobClient()
    spend0 = job.own()
    summ = LiveSummarizer(job)
    cases = {c.id: c for c in all_cases()}
    ids = [x for x in (a.only.split(",") if a.only else CASES) if x]
    jobs = [(cid, arm, rep) for rep in range(a.reps) for cid in ids for arm in a.arms.split(",")]
    random.Random(31).shuffle(jobs)
    lock = threading.Lock()
    rows = []

    def one(j):
        cid, arm, rep = j
        if arm == "harness":
            client, sc = job, None
        else:
            mk = ToolDedupMiddleware if arm == "harness+dedup" else \
                (lambda: MemoryCompressionMiddleware(summarize=summ))
            sc = SaverClient(job, mk)
            client = sc
        r = run_case(cases[cid], client, "harness", rep=rep, purpose=f"gap-1031-ctx-{arm}").as_dict()
        r["arm"] = arm
        if sc:
            r["est_tokens_before"], r["est_tokens_after"] = sc.est_before, sc.est_after
        r.pop("trace", None)
        with lock:
            rows.append(r)
            with (out / "runs.jsonl").open("a") as f:
                f.write(json.dumps(r) + "\n")
        print(cid, arm, rep, r["success"], r["calls"], r["prompt_tokens"], r["error"], flush=True)

    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(one, jobs))

    def agg(arm):
        rs = [r for r in rows if r["arm"] == arm]
        n = len(rs) or 1
        return {"runs": len(rs), "passed": sum(r["success"] for r in rs),
                "errors": sum(1 for r in rs if r["error"]),
                "prompt_tokens_per_run": round(sum(r["prompt_tokens"] for r in rs) / n, 1),
                "uncached_prompt_tokens_per_run": round(sum(r["prompt_tokens"] - r["cached_tokens"] for r in rs) / n, 1),
                "completion_tokens_per_run": round(sum(r["completion_tokens"] for r in rs) / n, 1),
                "calls_per_run": round(sum(r["calls"] for r in rs) / n, 2),
                "wall_s_per_run": round(sum(r["wall_s"] for r in rs) / n, 2), "label": "MEASURED"}
    res = {"spec": "wattnot.gap-1031.context-saver/1",
           "generated": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "model": "muse-spark-1.3", "cases": ids, "reps": a.reps,
           "arms": {arm: agg(arm) for arm in a.arms.split(",")},
           "summariser": {"model": SUMM_MODEL, "calls": summ.calls,
                          "prompt_tokens": summ.prompt_tokens,
                          "completion_tokens": summ.completion_tokens, "label": "MEASURED",
                          "note": "Llama 3.1 8B is not served on api.meta.ai; Muse Spark used."},
           "spend_usd": round(job.own() - spend0, 4), "spend_label": "MEASURED (Meta spend ledger)"}
    (out / "results.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
