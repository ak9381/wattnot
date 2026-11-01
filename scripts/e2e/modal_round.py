"""One live Modal-only matrix round (r04..r06): Muse Spark Open on WattNot's Modal Shared Endpoint.

Modal runs the GPUs behind a per-token Shared Endpoint, so there is no GPU to pick, no idle cost
and no app to tear down; there is also no NVML meter, so energy is ESTIMATE from tokens.
Billed against the workspace's hackathon credits (checked with `modal billing summary`).

Every round runs our set (92 tasks), GSM8K (first 60) and HumanEval (first 40), once without
WattNot and once with it. Muse Spark grades every final answer as a second opinion; the
deterministic check always decides pass or fail.

Arms (the levers that work through a shared per-token API; no GPU access on a shared endpoint):
  without_wattnot - every request at reasoning effort "medium".
  with_wattnot    - effort "low" first; step up to "medium" only when the fixed check fails
                    (the hosted routing lever, same rule as wattnot.spark_open.hosted_ab).

What is what:
  accuracy, tokens, USD (tokens x list price), latency  MEASURED
  energy (Wh, J per correct answer)                     ESTIMATE (tokens -> FLOPs -> J,
                                                        wattnot.cloud.energy; no GPU meter on a
                                                        hosted API)

    PYTHONPATH=.:src python scripts/e2e/modal_round.py --round r04 [--limit 3] [--no-spark]

Writes out/matrix/<round>.json (the dashboard's Run matrix reads it) and raw files under
data/modal-1101/<round>/ (every call, every answer with check and Spark verdict). Keys come
from the environment only and are never printed.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import os
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from wattnot.cloud import MODEL  # noqa: E402
from wattnot.cloud import energy as cloud_energy  # noqa: E402
from wattnot.cloud.efforts import max_tokens_for  # noqa: E402
from wattnot.cloud.pricing import Rates, cost_usd  # noqa: E402
from wattnot.cloud.transport import CloudError, ModalLiveTransport, Reply as CReply, Request  # noqa: E402
from wattnot.longrun.grade import grade_one  # noqa: E402
from wattnot.longrun.spark_grade import SparkGrader, load_config  # noqa: E402
from wattnot.longrun.tasks import build  # noqa: E402
from wattnot.proof.client import Reply  # noqa: E402
from wattnot.spark_open.h2h import BudgetStop, SpendCap, worst_case_usd  # noqa: E402

ARMS = ("without_wattnot", "with_wattnot")
PUB_BUDGET = {"gsm8k": 512, "humaneval": 768}
RATES = Rates()
SOFT_META_USD = 100.0


def _bench():
    spec = importlib.util.spec_from_file_location("public_bench_ab", ROOT / "scripts" / "public_bench_ab.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Calls:
    """Every Modal call: tokens, USD, latency. Thread-safe."""

    def __init__(self):
        self.rows, self.lock = [], threading.Lock()
        self.errors = 0

    def add(self, row):
        with self.lock:
            self.rows.append(row)
            if row["status"] != 200:
                self.errors += 1


class LockedTransport:
    def __init__(self, tr):
        self.tr, self.lock, self.model = tr, threading.Lock(), tr.model

    def __call__(self, req):
        with self.lock:      # keeps the transport's own spacing between call starts
            self.tr._throttle()
            interval, self.tr.min_interval_s = self.tr.min_interval_s, 0.0
        try:
            return self.tr(req)
        except CloudError as e:  # one failed call is a failed answer, never a dead round
            return CReply(599, {"error": str(e)[:200]}, 0.0)
        finally:
            self.tr.min_interval_s = interval


class SafeCap:
    """SpendCap behind a lock (its file update is read-modify-write)."""

    def __init__(self, cap):
        self.cap, self.lock = cap, threading.Lock()

    @property
    def session_usd(self):
        return self.cap.session_usd

    def check(self, w):
        with self.lock:
            self.cap.check(w)

    def add(self, usd):
        with self.lock:
            self.cap.add(usd)


class ModalChat:
    """longrun Chat interface (messages, max_tokens) -> Reply, over Modal at one effort."""

    def __init__(self, tr, effort, cap, calls, arm, item_ctx):
        self.tr, self.effort, self.cap, self.calls, self.arm = tr, effort, cap, calls, arm
        self.item = item_ctx
        self.context_limit = None

    def __call__(self, messages, max_tokens):
        mt = max_tokens_for(self.effort, max_tokens)
        prompt = " ".join(str(m.get("content") or "") for m in messages)
        self.cap.check(worst_case_usd(RATES, prompt, mt))
        body = {"model": self.tr.model, "reasoning_effort": self.effort, "max_tokens": mt, "temperature": 0,
                "messages": messages}
        r = self.tr(Request(f"{self.item}|{self.effort}", body))
        d = r.body or {}
        u = d.get("usage") or {}
        pt, ct = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
        cached = int((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0)
        rt = (u.get("completion_tokens_details") or {}).get("reasoning_tokens")
        usd = cost_usd(RATES, pt, cached, ct) if r.status == 200 else 0.0
        if usd:
            self.cap.add(usd)
        ch = (d.get("choices") or [{}])[0]
        text = ((ch.get("message") or {}).get("content") or "") if r.status == 200 else ""
        fin = (ch.get("finish_reason") or "stop") if r.status == 200 else "error"
        self.calls.add({"item": self.item, "arm": self.arm, "effort": self.effort, "status": r.status,
                        "latency_s": round(r.latency_s, 4), "prompt_tokens": pt, "cached_tokens": cached,
                        "completion_tokens": ct, "reasoning_tokens": rt, "usd": round(usd, 8),
                        "finish": fin, "retries": r.retries, "sent_at": r.sent_at})
        return Reply(r.latency_s, ct, text, fin, int(rt or 0), rt is not None)


class Capture:
    """Spark-hook shape; keeps the answer text so Spark can grade the final attempt later."""

    def __init__(self):
        self.last = None

    def __call__(self, item_id, dataset, prompt, answer, det):
        self.last = {"id": str(item_id), "dataset": dataset, "prompt": prompt, "answer": answer,
                     "deterministic": bool(det)}
        return self.last


def items_for(b, limit):
    out = []
    for t in build():
        out.append(("ours", t.id, t))
    for name in ("gsm8k", "humaneval"):
        for x in b.load(name):
            out.append((name, str(x.get("task_id", x.get("question", "")[:40])), x))
    if limit:
        by = {}
        for it in out:
            by.setdefault(it[0], []).append(it)
        out = [it for v in by.values() for it in v[:limit]]
    return out


def attempt(b, tr, cap, calls, arm, effort, ds, iid, obj):
    chat = ModalChat(tr, effort, cap, calls, arm, f"{ds}:{iid}")
    cap_ = Capture()
    t0 = time.perf_counter()
    if ds == "ours":
        g = grade_one(obj, chat, cap_)
        ok, tier = g.ok, g.tier
        rec = cap_.last or {"id": iid, "dataset": obj.tier, "prompt": obj.prompt, "answer": "", "deterministic": ok}
        finish = "length" if g.truncated else str(g.detail.get("finish") or "stop")
    else:
        pf, gf = b.grader(ds)
        prompt = pf(obj)
        r = chat([{"role": "user", "content": prompt}], PUB_BUDGET[ds])
        ok = r.finish != "error" and r.finish != "length" and bool(gf(obj, r.text))
        tier = ds
        rec = {"id": iid, "dataset": ds, "prompt": prompt, "answer": r.text, "deterministic": ok}
        finish = r.finish
    rec.update(set=ds, tier=tier, effort=effort, finish=finish, wall_s=round(time.perf_counter() - t0, 4))
    return rec


def step_up_reason(rec) -> str:
    """Why a low answer was not usable, so a step-up to medium is recorded with its cause.

    length: the low call ran out of tokens (finish=length / context-limit) - a runaway cut by the
            low cap, not a real low-confidence miss.
    format: the call stopped but produced no parseable answer at all.
    wrong:  a complete answer that failed the deterministic check.
    """
    if rec.get("finish") in ("length", "context-limit"):
        return "length"
    if not str(rec.get("answer") or "").strip():
        return "format"
    return "wrong"


def run_item(b, tr, cap, calls, arm, it):
    ds, iid, obj = it
    if arm == "without_wattnot":
        r = attempt(b, tr, cap, calls, arm, "medium", ds, iid, obj)
        r["tries"] = ["medium"]
        return r
    r = attempt(b, tr, cap, calls, arm, "low", ds, iid, obj)
    tries = ["low"]
    reason = None
    # Keep the low answer when it is usable; only step up when it is not, and record WHY
    # (length/format/wrong) - same rule as scripts/e2e/baseten_round.py.
    if not r["deterministic"]:
        reason = step_up_reason(r)
        r2 = attempt(b, tr, cap, calls, arm, "medium", ds, iid, obj)
        r2["wall_s"] = round(r2["wall_s"] + r["wall_s"], 4)
        r, tries = r2, ["low", "medium"]
    r["tries"] = tries
    if reason:
        r["step_up_reason"] = reason
    return r


def pctl(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(round(q * (len(s) - 1))))], 4)


def arm_summary(answers, rows):
    pt = sum(r["prompt_tokens"] for r in rows)
    cached = sum(r["cached_tokens"] for r in rows)
    ct = sum(r["completion_tokens"] for r in rows)
    usd = sum(r["usd"] for r in rows)
    lo, hi = cloud_energy.wh_range(pt, cached, ct)
    wh = (lo + hi) / 2
    correct = sum(a["deterministic"] for a in answers)
    per = {}
    for a in answers:
        p = per.setdefault(a["set"], [0, 0])
        p[0] += a["deterministic"]
        p[1] += 1
    walls = [a["wall_s"] for a in answers]
    lat = [r["latency_s"] for r in rows if r["status"] == 200]
    return {"answers": len(answers), "correct": correct,
            "accuracy": round(correct / len(answers), 4) if answers else None,
            "per_set": {k: {"correct": v[0], "answers": v[1], "accuracy": round(v[0] / v[1], 4)} for k, v in per.items()},
            "calls": len(rows), "errors": sum(r["status"] != 200 for r in rows),
            "tokens": {"prompt": pt, "cached": cached, "completion": ct, "label": "MEASURED"},
            "usd": {"value": round(usd, 6), "label": "MEASURED", "method": "tokens x Modal list price (same as Baseten)"},
            "energy_wh": {"value": round(wh, 4), "low": round(lo, 4), "high": round(hi, 4), "label": "ESTIMATE",
                          "method": cloud_energy.METHOD},
            "j_per_correct": {"value": round(wh * 3600 / correct, 2) if correct else None, "label": "ESTIMATE"},
            "item_latency_s": {"p50": pctl(walls, 0.5), "p99": pctl(walls, 0.99), "label": "MEASURED"},
            "call_latency_s": {"p50": pctl(lat, 0.5), "p99": pctl(lat, 0.99),
                               "mean": round(statistics.mean(lat), 4) if lat else None, "label": "MEASURED"},
            "stepped_up": sum(len(a.get("tries", [])) > 1 for a in answers)}


def meta_total():
    from wattnot.meta.ledger import SpendLedger
    return SpendLedger().total()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", required=True, choices=["r04", "r05", "r06"])
    ap.add_argument("--limit", type=int, default=0, help="items per set (smoke test)")
    ap.add_argument("--conc", type=int, default=4)
    ap.add_argument("--cap-usd", type=float, default=15.0, help="Modal hard stop for this round")
    ap.add_argument("--no-spark", action="store_true")
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "matrix")
    ap.add_argument("--raw", type=Path, default=ROOT / "data" / "modal-1101")
    a = ap.parse_args(argv)

    cfg = load_config()
    b = _bench()
    items = items_for(b, a.limit)
    m0 = meta_total()
    worst_meta = len(items) * 2 * 0.0021575
    if not a.no_spark and m0 + worst_meta > SOFT_META_USD:
        print(f"[stop] Meta ${m0:.2f} + worst case ${worst_meta:.2f} would cross the ${SOFT_META_USD:.0f} soft cap")
        return 3
    raw = a.raw / a.round
    raw.mkdir(parents=True, exist_ok=True)
    cap = SafeCap(SpendCap(a.cap_usd, raw / "modal_spend.json"))
    lt = ModalLiveTransport()
    lt.timeout_s, lt.max_retries = 240.0, 2
    tr = LockedTransport(lt)
    tag0 = "smoke-" if a.limit else ""
    live_ans = open(raw / f"{tag0}answers.partial.jsonl", "a")
    calls = Calls()
    started = datetime.now(UTC).isoformat()
    print(f"[{a.round}] {len(items)} items x 2 arms, Meta ledger ${m0:.4f} (MEASURED)", flush=True)
    answers = {}
    stop = None
    for arm in ARMS:
        t0 = time.time()
        res = []
        with ThreadPoolExecutor(a.conc) as ex:
            futs = [ex.submit(run_item, b, tr, cap, calls, arm, it) for it in items]
            for i, f in enumerate(futs):
                try:
                    r = f.result()
                except BudgetStop as e:
                    stop = f"Modal hard stop: {e}"
                    continue
                except Exception as e:  # noqa: BLE001 - recorded, the round goes on
                    r = {"id": items[i][1], "set": items[i][0], "dataset": items[i][0], "deterministic": False, "answer": "",
                         "prompt": "", "error": f"{type(e).__name__}", "tries": [], "wall_s": 0.0}
                r["arm"] = arm
                res.append(r)
                live_ans.write(json.dumps(r) + "\n")
                live_ans.flush()
                if (i + 1) % 20 == 0:
                    print(f"[{a.round}] {arm} {i+1}/{len(items)} errors={calls.errors} "
                          f"modal=${cap.session_usd:.4f}", flush=True)
                if calls.errors >= 25 and calls.errors > 0.2 * len(calls.rows):
                    stop = "repeated Modal errors"
                    for g in futs:
                        g.cancel()
                    break
        answers[arm] = res
        print(f"[{a.round}] {arm} done in {time.time()-t0:.0f}s: "
              f"{sum(x['deterministic'] for x in res)}/{len(res)} correct", flush=True)
        if stop:
            break

    # Spark grades every final answer (second opinion; deterministic decides)
    spark = None
    if not a.no_spark and not stop:
        spark = SparkGrader(model=cfg.get("spark_grade_model"), round_id=a.round)
        if spark.mock:
            print("[stop] WATTNOT_META_MOCK is set; refusing a live round with MOCK grades")
            return 4
        def g(rec):
            sp = SparkGrader.__call__  # noqa: F841
            return spark_one(spark, rec)
        with ThreadPoolExecutor(4) as ex:
            for arm in ARMS:
                for rec, out in zip(answers.get(arm, []), ex.map(lambda r: spark_one(spark, r), answers.get(arm, []))):
                    rec.update(spark=out["spark"], spark_reason=out["spark_reason"], spark_label=out["spark_label"],
                               disagree=out["disagree"])
    m1 = meta_total()

    rows = {arm: [r for r in calls.rows if r["arm"] == arm] for arm in ARMS}
    summ = {arm: arm_summary(answers.get(arm, []), rows[arm]) for arm in ARMS}
    all_ans = [x for arm in ARMS for x in answers.get(arm, [])]
    graded = [x for x in all_ans if x.get("spark") is not None]
    dis = [x for x in graded if x["spark"] != x["deterministic"]]
    wo, wi = summ["without_wattnot"], summ["with_wattnot"]
    pct = lambda x, y: round(100 * (x - y) / x, 2) if x else None
    doc = {
        "round": a.round, "leg": "modal", "status": "done" if not stop else "stopped",
        "label": "MEASURED", "model": lt.model, "where": "Modal Shared Endpoint (Muse Spark Open)", "billing": "Modal hackathon credits",
        "started_utc": started, "ended_utc": datetime.now(UTC).isoformat(), "stop_reason": stop,
        "smoke": bool(a.limit),
        "note": ("accuracy, tokens, USD and latency MEASURED; energy ESTIMATE from tokens (hosted API, "
                 "no GPU meter); deterministic checks decide, Muse Spark grades beside them"),
        "levers": {"without_wattnot": "effort medium for every request",
                   "with_wattnot": "effort low first, step up to medium only when the fixed check fails"},
        "results": {
            "accuracy_without": wo["accuracy"], "accuracy_with": wi["accuracy"],
            "j_per_correct_without": wo["j_per_correct"]["value"], "j_per_correct_with": wi["j_per_correct"]["value"],
            "energy_saved_pct": {"value": pct(wo["energy_wh"]["value"], wi["energy_wh"]["value"]), "label": "ESTIMATE"},
            "usd_saved_pct": {"value": pct(wo["usd"]["value"], wi["usd"]["value"]), "label": "MEASURED"},
            "per_arm": summ},
        "spend": {"modal_usd": {"value": round(cap.session_usd, 6), "label": "MEASURED",
                                  "method": "tokens x list price (not yet checked against the invoice)"},
                  "meta_usd": {"value": round(m1 - m0, 6), "label": "MEASURED", "source": "Meta spend ledger"},
                  "meta_ledger_after_usd": round(m1, 4)},
        "spark_summary": {"label": "LIVE" if spark else None, "answers": len(all_ans), "spark_graded": len(graded),
                          "agreement": round(1 - len(dis) / len(graded), 4) if graded else None,
                          "disagreements": len(dis), "stopped": spark.stopped if spark else "not run"},
        "answers": [{k: x.get(k) for k in ("id", "dataset", "set", "arm", "deterministic", "spark", "spark_reason",
                                            "tries", "step_up_reason", "wall_s")} for x in all_ans],
        "raw_dir": str(raw.relative_to(ROOT)) if raw.is_relative_to(ROOT) else str(raw),
    }
    out = a.out if not a.limit else raw / "smoke-matrix"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{a.round}.json").write_text(json.dumps(doc, indent=1))
    tag = "smoke-" if a.limit else ""
    with open(raw / f"{tag}calls.jsonl", "w") as f:
        for r in calls.rows:
            f.write(json.dumps(r) + "\n")
    with open(raw / f"{tag}answers.jsonl", "w") as f:
        for x in all_ans:
            f.write(json.dumps(x) + "\n")
    print(json.dumps({"round": a.round, "status": doc["status"], "acc": [wo["accuracy"], wi["accuracy"]],
                      "wh": [wo["energy_wh"]["value"], wi["energy_wh"]["value"]],
                      "p50": [wo["item_latency_s"]["p50"], wi["item_latency_s"]["p50"]],
                      "modal_usd": round(cap.session_usd, 4), "meta_usd": round(m1 - m0, 4),
                      "agree": doc["spark_summary"]["agreement"], "errors": calls.errors, "stop": stop}), flush=True)
    return 0 if not stop else 2


def spark_one(spark, rec):
    return spark(rec["id"], rec["dataset"], rec["prompt"], rec["answer"], rec["deterministic"])


if __name__ == "__main__":
    sys.exit(main())
