"""Live answer check for the GPU helper on a real GPU (MEASURED).

At stock it asks a small fixed sample once and stores the answers, the NVML energy per answer and
the latencies. At each lower cap it re-asks the same sample and keeps the cap only if every answer
agrees with stock, energy per answer went down, and p99 latency rose no more than ``p99_limit`` %.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

SAMPLE = [
    "What is 17 * 23? Reply with the number only.",
    "A GPU draws 350 W for 2 hours. How many kWh is that? Number only.",
    "What is 144 / 12? Reply with the number only.",
    "How many seconds are in 3 minutes? Number only.",
    "What is 2 to the power of 10? Number only.",
]


class AnswerCheck:
    def __init__(self, ask, gpu, prompts=None, p99_limit: float = 10.0, concurrency: int = 1):
        """ask(prompt) -> answer text (one model call); gpu: NVML-shaped (energy counter in mJ).
        concurrency > 1 sends the sample in parallel (needed to see a batching setting at all)."""
        self.ask, self.gpu, self.prompts, self.p99_limit = ask, gpu, list(prompts or SAMPLE), p99_limit
        self.concurrency = max(1, int(concurrency))
        self.ref: dict | None = None

    def _round(self) -> dict:
        e0 = self.gpu.nvmlDeviceGetTotalEnergyConsumption(None)
        def one(p):
            t = time.perf_counter()
            a = self.ask(p)
            return a, time.perf_counter() - t
        if self.concurrency > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(self.concurrency) as ex:
                out = list(ex.map(one, self.prompts))
        else:
            out = [one(p) for p in self.prompts]
        answers, lat = [a for a, _ in out], [t for _, t in out]
        j = (self.gpu.nvmlDeviceGetTotalEnergyConsumption(None) - e0) / 1000
        return {"answers": answers, "n": len(answers), "joules_per_answer": round(j / max(1, len(answers)), 2),
                "p99_s": max(lat) if lat else 0.0}

    def stock(self) -> dict:
        self.ref = self._round()
        return self.ref

    def at(self, cap=None) -> dict:
        from wattnot.gateway.monitor import agree
        if self.ref is None:
            self.stock()
        r, ref = self._round(), self.ref
        ok = sum(agree(a, b) for a, b in zip(r["answers"], ref["answers"]))
        e_pct = round(100 * (r["joules_per_answer"] - ref["joules_per_answer"]) / ref["joules_per_answer"], 1) \
            if ref["joules_per_answer"] else None
        p99 = round(100 * (r["p99_s"] - ref["p99_s"]) / ref["p99_s"], 1) if ref["p99_s"] else 0.0
        why = []
        if ok < r["n"]:
            why.append(f"{r['n'] - ok} of {r['n']} answers changed")
        if e_pct is None or e_pct >= 0:
            why.append("energy per answer did not go down")
        if p99 > self.p99_limit:
            why.append(f"slowest answer +{p99}% (limit {self.p99_limit}%)")
        return {"accepted": not why, "energy_change_pct": e_pct, "p99_change_pct": p99,
                "answers_same": f"{ok}/{r['n']}", "why_not": why,
                "label": "MEASURED (NVML energy, live re-ask)"}


def gateway_asker(cfg: dict):
    """ask(prompt) through the configured upstream at the baseline tier (stock answers)."""
    from wattnot.agentwaste.server import tier_body
    from wattnot.gateway.build import http_upstream
    up = http_upstream(cfg)
    base = next(t for t in cfg["tiers"] if t["name"] == cfg["baseline"]["tier"])

    def ask(prompt: str) -> str:
        r = up(tier_body({"messages": [{"role": "user", "content": prompt}], "max_completion_tokens": 512}, base),
               {"x-wattnot-kind": "node-check"})
        return str(((r.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    return ask


# ---------------------------------------------------------------- the harness's own cap rule
PROOF_V1 = Path(__file__).resolve().parents[3] / "workloads" / "proof-v1.jsonl"


def proof_prompts(n: int | None = None) -> list[dict]:
    """The proof-v1 workload the H100 sweep judged 375 W on (111 prompts), first n of them."""
    rows = [json.loads(ln) for ln in PROOF_V1.read_text().splitlines() if ln.strip()]
    return rows[:n] if n else rows


class HarnessCapCheck:
    """The cap rule of the H100 sweep that picked 375 W (wattnot.proof.session.summarize):
    interleaved stock/cap blocks of the proof-v1 prompts at concurrency 32 (the sweep's default),
    p99 latency per block (wattnot.proof.stats.percentile), accept when the MEAN per-pair p99
    change is <= p99_limit (10%, wattnot.settings default) and energy is clearly down by
    wattnot.proof.session.energy_clearly_down (with 2-3 pairs every pair below max(3%, 2x stock cv)).
    Quality uses the sweep's harness graders (gates.fixed.fixed_checks), not exact text."""

    def __init__(self, ask, gpu, set_cap, stock_w: int, n: int = 111, pairs: int = 2,
                 concurrency: int = 32, p99_limit: float = 10.0):
        self.ask, self.gpu, self.set_cap, self.stock_w = ask, gpu, set_cap, int(stock_w)
        self.items, self.pairs, self.concurrency, self.p99_limit = proof_prompts(n), pairs, concurrency, p99_limit
        self.ref = None

    def _block(self, w: int) -> dict:
        from concurrent.futures import ThreadPoolExecutor
        from wattnot.proof.stats import percentile
        self.set_cap(w)
        e0 = self.gpu.nvmlDeviceGetTotalEnergyConsumption(None)

        def one(it):
            t = time.perf_counter()
            a = self.ask(it["prompt"], int(it.get("max_tokens") or 512))
            return a, time.perf_counter() - t
        with ThreadPoolExecutor(self.concurrency) as ex:
            out = list(ex.map(one, self.items))
        j = (self.gpu.nvmlDeviceGetTotalEnergyConsumption(None) - e0) / 1000
        lat = [t for _, t in out]
        return {"answers": [a for a, _ in out], "j_per_req": j / max(1, len(out)),
                "p99_s": percentile(lat, 99), "cap_w": w}

    def stock(self) -> dict:
        return {}

    def at(self, cap) -> dict:
        from wattnot.gateway.monitor import agree
        from wattnot.proof.session import energy_clearly_down, stock_noise
        from wattnot.proof.stats import ci95, mean_sd
        stock, capped = [], []
        try:
            for r in range(self.pairs):
                order = [("stock", self.stock_w), ("cap", int(cap))]
                if r % 2:
                    order = order[::-1]   # cap first on odd rounds, as the sweep does
                for arm, w in order:
                    (stock if arm == "stock" else capped).append(self._block(w))
        finally:
            self.set_cap(int(cap))        # the caller decides what stays; it restores stock on reject
        pct = lambda c, s: 100 * (c - s) / s if s else float("nan")  # noqa: E731
        d_e = [pct(c["j_per_req"], s["j_per_req"]) for s, c in zip(stock, capped)]
        d_p = [pct(c["p99_s"], s["p99_s"]) for s, c in zip(stock, capped)]
        e_mean, _ = mean_sd(d_e)
        _, e_half = ci95(d_e)
        p_mean, _ = mean_sd(d_p)
        # Quality exactly as the proof sweep scored proof-v1: harness graders (gates.answers via
        # gates.fixed: math = same number, qa = alias/word match, code = run the tests), pooled
        # stock vs pooled cap, cap pass rate no more than MAX_DROP_PTS below stock. Not exact text.
        from wattnot.gates.fixed import fixed_checks
        from wattnot.longrun.holdout import paired_summary
        from wattnot.proof.rig import pooled_items
        from wattnot.proof.session import MAX_DROP_PTS
        from wattnot.gates.fixed import item_checks, run_check
        from wattnot.runner.workload import load_workload
        wl = list(load_workload(PROOF_V1))[:len(self.items)]
        pool = lambda bs: {f"{it.id}#{r}": a for r, b in enumerate(bs)  # noqa: E731
                           for it, a in zip(wl, b["answers"])}
        q = fixed_checks(pool(stock), pool(capped), pooled_items(wl, len(stock)), max_drop_pts=MAX_DROP_PTS)
        oks = lambda b: [all(run_check(c, a) for c in item_checks(it)) for it, a in zip(wl, b["answers"])]  # noqa: E731
        paired = paired_summary(oks(stock[0]), oks(capped[-1]))
        n = len(wl)
        why = [] if q.passed else [f"quality: {q.reason}"]
        if not energy_clearly_down(d_e, e_mean, e_half, stock_noise(stock)["cv_pct"]):
            why.append("energy change not clearly below zero")
        if p_mean > self.p99_limit:
            why.append(f"p99 up {p_mean:.1f}% (limit {self.p99_limit:.0f}%)")
        return {"accepted": not why, "energy_change_pct": round(e_mean, 1), "p99_change_pct": round(p_mean, 1),
                "per_pair_energy_pct": [round(x, 1) for x in d_e], "per_pair_p99_pct": [round(x, 1) for x in d_p],
                "quality": {"stock_pct": q.detail["baseline_pct"], "cap_pct": q.detail["candidate_pct"],
                            "max_drop_pts": MAX_DROP_PTS, "passed": q.passed, "reason": q.reason,
                            "holdout_paired": paired},
                "why_not": why, "n": n, "pairs": self.pairs,
                "rule": "proof sweep: harness graders (fixed_checks, <=1 pt drop), mean p99 <= +10%, "
                        "energy_clearly_down, concurrency 32",
                "label": "MEASURED (NVML energy, interleaved stock/cap blocks)"}


def harness_asker(cfg: dict):
    """ask(prompt, max_tokens) at the baseline tier with the holdout's sampling (temperature 0, seed)."""
    from wattnot.agentwaste.server import tier_body
    from wattnot.gateway.build import http_upstream
    up = http_upstream(cfg)
    base = next(t for t in cfg["tiers"] if t["name"] == cfg["baseline"]["tier"])

    def ask(prompt: str, max_tokens: int = 512) -> str:
        r = up(tier_body({"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
                          "temperature": 0, "seed": 1234}, base), {"x-wattnot-kind": "node-check"})
        return str(((r.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    return ask
