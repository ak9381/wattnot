"""Live answer check for the GPU helper on a real GPU (MEASURED).

At stock it asks a small fixed sample once and stores the answers, the NVML energy per answer and
the latencies. At each lower cap it re-asks the same sample and keeps the cap only if every answer
agrees with stock, energy per answer went down, and p99 latency rose no more than ``p99_limit`` %.
"""
from __future__ import annotations

import time

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
