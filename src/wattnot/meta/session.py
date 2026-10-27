"""A real Muse Spark session over WattNot's fixtures and workloads.

    1. next-knob     the agent drives the toy surfaces (gpu-power, moe-routing, hosted-effort)
    2. effort ladder the agent orders the reasoning-effort levels per prompt class; we run the
                     public `cloud-effort-v1` set on the live model in that order, check each
                     answer with the workload's regex checks, and stop a class at the first
                     level that passes. Cost and tokens are measured from the API's usage.
    3. coding set    the public Muse Code set at `high` vs the cheapest level that passed,
                     judged pairwise by Muse Spark through the existing judge gate
    4. receipts      plain-words notes on the fixture receipts

Every call goes through the spend guard. The run stops cleanly when the guard refuses.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from wattnot.gates.fixed import item_checks, run_check
from wattnot.gates.judge import agreement, judge, load_judge_set, parse_verdicts, request_body
from wattnot.meta import agent as ag
from wattnot.meta.agent import MODEL
from wattnot.meta.client import GuardedClient, MetaError
from wattnot.meta.ledger import BudgetExceeded, SpendLedger, cost_usd
from wattnot.meta.scenarios import SCENARIOS
from wattnot.runner.workload import load_workload

ROOT = Path(__file__).resolve().parents[3]
CLOUD_SET = ROOT / "workloads" / "cloud-effort-v1.jsonl"
CODE_SET = ROOT / "workloads" / "muse-code-v1.jsonl"
RECEIPTS = ROOT / "tests" / "fixtures" / "receipts"
REFERENCE = "high"
ALLOWANCE = {"minimal": 800, "low": 1500, "medium": 3500, "high": 7000}  # reasoning headroom


@dataclass
class Session:
    client: GuardedClient
    ledger: SpendLedger
    log: Callable[[str], None] = print
    repeats: int = 3
    workers: int = 6
    full_ladder: bool = False  # also run the levels the agent's order would have skipped
    cache_dir: Path | None = None
    out: dict = field(default_factory=dict)

    # ---- one answer ----------------------------------------------------------------------

    def answer(self, item, effort: str, purpose: str) -> dict:
        body = {"model": MODEL, "reasoning_effort": effort,
                "max_tokens": item.max_tokens + ALLOWANCE[effort],
                "messages": [{"role": "user", "content": item.prompt}]}
        t0 = time.monotonic()
        try:
            raw = self.client(body, purpose=purpose)
        except BudgetExceeded:
            raise
        except MetaError as e:
            return {"id": item.id, "effort": effort, "text": "", "error": str(e), "usd": 0.0,
                    "latency_s": time.monotonic() - t0, **{"input": 0, "output": 0,
                                                            "reasoning": 0}}
        u = ag.usage_of(raw)
        text = raw["choices"][0]["message"].get("content") or ""
        return {"id": item.id, "effort": effort, "text": text, "latency_s": time.monotonic() - t0,
                "usd": cost_usd(MODEL, u["input"], u["output"],
                                (raw.get("usage", {}).get("prompt_tokens_details") or {})
                                .get("cached_tokens", 0)),
                "finish": raw["choices"][0].get("finish_reason"), **u}

    def _many(self, jobs):
        with ThreadPoolExecutor(self.workers) as ex:
            return list(ex.map(lambda j: self.answer(*j), jobs))

    # ---- 1. next knob --------------------------------------------------------------------

    def next_knob(self, rounds: int = 6) -> dict:
        res = {}
        for name, sc in SCENARIOS.items():
            state, history, trace = dict(sc.start), [], []
            base = sc.run(state)
            history.append({"setting": {}, "result": "baseline", "saving_pct": base["saving_pct"],
                            "p99_delta_pct": base["p99_delta_pct"],
                            "quality_pct": base["quality_pct"], "passed": True})
            for i in range(rounds):
                try:
                    d = ag.next_trial(self.client, sc.prompt_view() | {"current": dict(state)},
                                      history)
                except (ValueError, KeyError, json.JSONDecodeError, MetaError) as e:
                    trace.append({"round": i + 1, "error": str(e)[:120]})
                    break
                if d["kind"] == "stop":
                    trace.append({"round": i + 1, "kind": "stop", "reason": d["reason"]})
                    break
                trial = {**state, d["knob"]: d["value"]}
                m = sc.run(trial)
                if m["passed"]:
                    state = trial
                history.append({"setting": {d["knob"]: d["value"]},
                                "saving_pct": m["saving_pct"],
                                "p99_delta_pct": m["p99_delta_pct"],
                                "quality_pct": m["quality_pct"], "passed": m["passed"]})
                trace.append({"round": i + 1, "knob": d["knob"], "value": d["value"],
                              "reason": d["reason"], "passed": m["passed"],
                              "saving_pct": m["saving_pct"], "tokens": d["usage"]})
            final = sc.run(state)
            res[name] = {"label": "SIMULATED surface, real agent decisions", "final": state,
                         "saving_pct": final["saving_pct"], "trace": trace}
            self.log(f"next-knob {name}: saving {final['saving_pct']}% in {len(trace)} rounds, "
                     f"${self.ledger.total():.3f} spent")
        return res

    # ---- 2. effort ladder on the checked set ---------------------------------------------

    def effort_ladder(self) -> dict:
        wl = load_workload(CLOUD_SET)
        classes: dict[str, list] = defaultdict(list)
        for it in wl:
            classes[it.meta["class"]].append(it)
        summary = {c: {"items": len(v), "avg_prompt_chars": round(sum(len(i.prompt) for i in v)
                                                                  / len(v)),
                       "avg_answer_budget_tokens": round(sum(i.max_tokens for i in v) / len(v))}
                   for c, v in classes.items()}
        plan = ag.effort_order(self.client, summary)
        self.log(f"effort order: {plan['orders']}")

        def passes(a: Mapping, it) -> bool:
            return bool(a["text"]) and all(run_check(c, a["text"]) for c in item_checks(it))

        def run_level(items, effort):
            rows = self._many([(it, effort, f"effort-ladder:{effort}")
                               for it in items for _ in range(self.repeats)])
            byid = {it.id: it for it in items}
            ok = sum(passes(r, byid[r["id"]]) for r in rows)
            return {"pass_rate": round(100 * ok / len(rows), 1), "n": len(rows),
                    "usd": round(sum(r["usd"] for r in rows), 5),
                    "output_tokens": sum(r["output"] for r in rows),
                    "reasoning_tokens": sum(r["reasoning"] for r in rows),
                    "mean_latency_s": round(sum(r["latency_s"] for r in rows) / len(rows), 2),
                    "errors": sum(1 for r in rows if r.get("error"))}

        res = {"order_note": plan["note"], "orders": plan["orders"], "repeats": self.repeats,
               "classes": {}}
        tot_ref = tot_pick = tot_best = 0.0
        for c, items in classes.items():
            ref = run_level(items, REFERENCE)
            levels = {REFERENCE: ref}
            pick = None
            for e in plan["orders"][c]:
                if e == REFERENCE:
                    continue
                if pick is not None and not self.full_ladder:
                    break
                levels[e] = run_level(items, e)
                if pick is None and levels[e]["pass_rate"] >= ref["pass_rate"] \
                        and levels[e]["errors"] == 0:
                    pick = e
            pick = pick or REFERENCE
            ok = [e for e, v in levels.items()
                  if v["pass_rate"] >= ref["pass_rate"] and v["errors"] == 0]
            best = min(ok, key=lambda e: levels[e]["usd"])
            res["classes"][c] = {"reference": REFERENCE, "picked": pick,
                                 "cheapest_passing": best, "levels": levels}
            tot_ref += ref["usd"]
            tot_pick += levels[pick]["usd"]
            tot_best += levels[best]["usd"]
            self.log(f"ladder {c}: picked {pick}, cheapest passing {best} "
                     f"(ref {ref['pass_rate']}% / {levels[pick]['pass_rate']}%), "
                     f"${self.ledger.total():.3f} spent")
        if tot_ref:
            res["cost_saving_vs_reference_pct"] = round(100 * (1 - tot_pick / tot_ref), 1)
            res["cost_saving_if_cheapest_passing_pct"] = round(100 * (1 - tot_best / tot_ref), 1)
        return res

    # ---- 3. coding set, judged -----------------------------------------------------------

    def _judge_send(self, purpose: str, effort: str = "low") -> Callable[[dict], dict]:
        """The judge gate's request, with room to reason (its default cap is too tight)."""

        return lambda b: self.client({**b, "reasoning_effort": effort, "max_tokens": 8000},
                                     purpose=purpose)

    def _cached_many(self, name: str, jobs):
        f = self.cache_dir / f"{name}.json" if self.cache_dir else None
        if f and f.exists():
            return json.loads(f.read_text())
        rows = self._many(jobs)
        if f:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(rows))
        return rows

    def coding_set(self, efforts=("low", "medium"), items: int = 48) -> dict:
        wl = load_workload(CODE_SET)
        sel = list(wl)[:items]
        base = self._cached_many(f"coding-{REFERENCE}",
                                 [(it, REFERENCE, f"coding-set:{REFERENCE}") for it in sel])
        bo = {r["id"]: r["text"] for r in base}
        usd_b = sum(r["usd"] for r in base)
        res = {"reference": REFERENCE, "usd_reference": round(usd_b, 5),
               "output_tokens_reference": sum(r["output"] for r in base), "levels": {}}
        for eff in efforts:
            cand = self._cached_many(f"coding-{eff}", [(it, eff, f"coding-set:{eff}")
                                                       for it in sel])
            co = {r["id"]: r["text"] for r in cand}
            rows = [{"id": it.id, "question": it.prompt} for it in sel if bo[it.id] and co[it.id]]
            g = judge(bo, co, rows, transport=self._judge_send(f"judge:coding-{eff}"))
            usd_c = sum(r["usd"] for r in cand)
            res["levels"][eff] = {
                "items": len(rows), "judge_passed": g.passed, "judge": g.reason,
                "judge_detail": {k: g.detail.get(k) for k in ("same", "better", "worse")},
                "usd": round(usd_c, 5), "output_tokens": sum(r["output"] for r in cand),
                "cost_saving_pct": round(100 * (1 - usd_c / usd_b), 1) if usd_b else None}
            self.log(f"coding {eff}: {g.reason}, ${self.ledger.total():.3f} spent")
        return res

    # ---- 3b. is the judge itself reliable? -----------------------------------------------

    def judge_calibration(self, efforts=("low", "medium")) -> dict:
        """Agreement with the human labels on the public judge-30 set, per judge effort."""

        items = load_judge_set()
        rows = [{"id": i["id"], "question": i["question"], "baseline": i["baseline"],
                 "candidate": i["candidate"]} for i in items]
        res = {}
        for eff in efforts:
            raw = self._judge_send(f"judge-calibration:{eff}", eff)(request_body(rows))
            verdicts = parse_verdicts(raw, [r["id"] for r in rows])
            res[eff] = agreement(items, verdicts)
            self.log(f"judge calibration {eff}: {res[eff]}")
        return res

    # ---- 4. receipts ---------------------------------------------------------------------

    def receipt_notes(self) -> dict:
        out = {}
        for p in sorted(RECEIPTS.glob("*.json")):
            out[p.stem] = ag.explain(self.client, json.loads(p.read_text()), p.stem)
        return out

    # ---- the session ---------------------------------------------------------------------

    def run(self, steps=("next-knob", "ladder", "coding", "judge", "receipts")) -> dict:
        t0 = time.time()
        try:
            if "next-knob" in steps:
                self.out["next_knob"] = self.next_knob()
            if "ladder" in steps:
                self.out["effort_ladder"] = self.effort_ladder()
            if "coding" in steps:
                self.out["coding_set"] = self.coding_set()
            if "judge" in steps:
                self.out["judge_calibration"] = self.judge_calibration()
            if "receipts" in steps:
                self.out["receipt_notes"] = self.receipt_notes()
        except BudgetExceeded as e:
            self.out["stopped"] = str(e)
        self.out["elapsed_s"] = round(time.time() - t0)
        self.out["spend"] = self.ledger.summary()
        return self.out

