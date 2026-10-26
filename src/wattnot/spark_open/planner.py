"""Plan the Spark Open search: about 32 tries inside the 3-4.5 h side run.

Grid: 4 quants x 4 hot-layer levels x 2 batch modes = 32.
- hot levels are fractions (0, 1/3, 2/3, 1) of the most layers whose experts fit
  in VRAM for that quant (ESTIMATE from file size / 62 layers);
- batch modes: "gpu" = op offload on with a big ubatch (warm tier active),
  "cpu" = --no-op-offload with ubatch 512 (CPU comparison).
Try 0 is the baseline. Tries are grouped by quant so each quant downloads and
loads once. Time per try is an ESTIMATE; tries past the budget are dropped
(lowest-priority first) and listed in `dropped`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from wattnot.spark_open.knobs import (
    DEFAULT_THREADS,
    N_LAYERS,
    QUANTS,
    STAGE_QUANTS,
    SparkKnobs,
    baseline,
)

VRAM_GB = 80.0
NON_EXPERT_GB = 12.0   # attention, shared experts, embeddings: ESTIMATE
KV_GB = 4.0            # 16k context: ESTIMATE
HOT_FRACTIONS = (0.0, 1 / 3, 2 / 3, 1.0)
BUDGET_H = 4.5
DOWNLOAD_GB_PER_MIN = 10.0  # ~170 MB/s from HF: ESTIMATE
FIRST_LOAD_MIN = 8.0        # cold read from disk into RAM
RELOAD_MIN = 2.0            # restart with the quant already in page cache
MEASURE_MIN = 2.5           # 10 short prompts


def compute_buffer_gb(ubatch: int) -> float:
    return 0.5 + ubatch / 1024  # ESTIMATE: grows with ubatch


def max_hot_layers(quant: str, ubatch: int = 4096) -> int:
    per_layer = QUANTS[quant].approx_gb / N_LAYERS
    free = VRAM_GB - NON_EXPERT_GB - KV_GB - compute_buffer_gb(ubatch)
    return max(0, min(N_LAYERS, int(free // per_layer)))


def vram_estimate_gb(k: SparkKnobs) -> float:
    per_layer = QUANTS[k.quant].approx_gb / N_LAYERS
    return round(NON_EXPERT_GB + KV_GB + compute_buffer_gb(k.ubatch) + k.hot_layers * per_layer, 1)


@dataclass
class Plan:
    tries: list[SparkKnobs]
    est_minutes: float
    budget_h: float
    dropped: list[SparkKnobs] = field(default_factory=list)
    label: str = "ESTIMATE"

    def describe(self) -> dict:
        return {"n_tries": len(self.tries), "est_hours": round(self.est_minutes / 60, 2),
                "budget_h": self.budget_h, "label": self.label,
                "dropped": [k.tag for k in self.dropped],
                "grid": "4 quants x 4 hot-layer levels x 2 batch modes"}


def _candidates(quants, gpu_ubatch: int, threads: int) -> list[SparkKnobs]:
    out = []
    for q in quants:
        top = max_hot_layers(q, gpu_ubatch)
        levels = sorted({round(top * f) for f in HOT_FRACTIONS})
        for hot in levels:
            out.append(SparkKnobs(q, N_LAYERS - hot, gpu_ubatch, True, threads))
            out.append(SparkKnobs(q, N_LAYERS - hot, 512, False, threads))
    return out


def estimate_minutes(tries: list[SparkKnobs]) -> float:
    total, seen = 0.0, set()
    for k in tries:
        if k.quant not in seen:
            seen.add(k.quant)
            total += QUANTS[k.quant].approx_gb / DOWNLOAD_GB_PER_MIN + FIRST_LOAD_MIN
        else:
            total += RELOAD_MIN
        total += MEASURE_MIN
    return total


def plan(quants=STAGE_QUANTS, *, budget_h: float = BUDGET_H, gpu_ubatch: int = 4096,
         threads: int = DEFAULT_THREADS) -> Plan:
    base = baseline()
    cands = [k for k in _candidates(quants, gpu_ubatch, threads) if k != base]
    # Order: baseline quant first, then the others; within a quant, GPU mode first.
    tries = [base, *cands] if base.quant in quants else cands
    dropped = []
    # Lowest priority last: drop CPU-comparison tries of the low quants first.
    while estimate_minutes(tries) > budget_h * 60 and len(tries) > 1:
        victim = next((k for k in reversed(tries) if not k.op_offload and k != base),
                      tries[-1])
        tries.remove(victim)
        dropped.append(victim)
    return Plan(tries, round(estimate_minutes(tries), 1), budget_h, dropped)
