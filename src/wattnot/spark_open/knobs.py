"""Spark Open knobs for the llama.cpp three-tier hybrid.

Every knob needs a `llama-server` restart, so a try is "restart with these flags".
Quants are the ready-made Unsloth GGUFs; nothing is re-quantized here.

Tiers, as llama.cpp actually does them (checked against common/arg.cpp and
ggml-cuda.cu on llama.cpp master, Oct 2026):

- hot:  layers whose expert tensors live in VRAM (`--n-cpu-moe N` keeps the
        experts of the first N layers in RAM; the rest stay on the GPU)
- warm: CPU-held expert layers when a batch has >= 32 tokens (prefill, batched
        decode). Op offload (on by default) copies the weights to the GPU and the
        GPU does the maths. `GGML_OP_OFFLOAD_MIN_BATCH` sets the 32.
- cold: the same CPU-held layers at batch < 32 (normal decode). The CPU does the
        maths. `--no-op-offload` makes every CPU-held layer cold (CPU comparison).

Pinning is per layer, not per expert: all 256 experts of a layer are one stacked
tensor (`blk.N.ffn_*_exps`), so `--n-cpu-moe` moves whole layers. Per-expert
pinning by router frequency is a later stretch (see placement.py).
"""

from __future__ import annotations

from dataclasses import dataclass

REPO = "unsloth/Muse-Spark-Open-816B-A42B-GGUF"
N_LAYERS = 62  # model card: 62 layers, 256 experts, top-8
OP_OFFLOAD_MIN_BATCH = 32  # ggml-cuda default (GGML_OP_OFFLOAD_MIN_BATCH)


@dataclass(frozen=True)
class Quant:
    name: str
    approx_gb: int     # file size, ESTIMATE from the HF repo listing
    agentic_ok: bool   # Unsloth: below UD-IQ2_S, agentic tasks degrade
    note: str = ""


QUANTS: dict[str, Quant] = {
    "UD-IQ1_S": Quant("UD-IQ1_S", 176, False, "low end: chat holds up, agentic degrades"),
    "UD-IQ1_M": Quant("UD-IQ1_M", 209, False, "below the agentic floor"),
    "UD-IQ2_XXS": Quant("UD-IQ2_XXS", 220, False, "below the agentic floor"),
    "UD-IQ2_S": Quant("UD-IQ2_S", 260, True, "default; agentic floor"),
}
DEFAULT_QUANT = "UD-IQ2_S"
AGENTIC_FLOOR = "UD-IQ2_S"
STAGE_QUANTS = ("UD-IQ2_S", "UD-IQ1_S", "UD-IQ1_M", "UD-IQ2_XXS")
UBATCH_CHOICES = (512, 2048, 4096)
DEFAULT_THREADS = 24


@dataclass(frozen=True)
class SparkKnobs:
    """One try. `n_cpu_moe` = layers whose experts stay in host RAM.

    62 puts every expert on the CPU (the same as `--cpu-moe`); lower numbers keep
    more layers' experts in VRAM (the hot tier).
    """

    quant: str = DEFAULT_QUANT
    n_cpu_moe: int = N_LAYERS
    ubatch: int = 512
    op_offload: bool = True
    threads: int = DEFAULT_THREADS
    # Later: per-expert pinning. Empty today; llama-server cannot apply it.
    pinned_experts: tuple[tuple[int, int], ...] = ()

    def __post_init__(self) -> None:
        if self.quant not in QUANTS:
            raise ValueError(f"quant must be one of {sorted(QUANTS)}")
        if not 0 <= self.n_cpu_moe <= N_LAYERS:
            raise ValueError(f"n_cpu_moe must be in 0..{N_LAYERS}")
        if not 32 <= self.ubatch <= 8192:
            raise ValueError("ubatch must be in 32..8192")
        if not 1 <= self.threads <= 512:
            raise ValueError("threads must be in 1..512")

    @property
    def hot_layers(self) -> int:
        return N_LAYERS - self.n_cpu_moe

    @property
    def flags(self) -> list[str]:
        """Warnings that travel with the try into its record."""

        out = []
        q = QUANTS[self.quant]
        if not q.agentic_ok:
            out.append(f"{q.name} is below the agentic floor ({AGENTIC_FLOOR})")
        if not self.op_offload:
            out.append("op offload off: CPU comparison, no warm tier")
        return out

    def tiers(self) -> dict:
        """The per-tier split this try configures. Layer counts are exact config,
        not measurements; how much work lands in warm vs cold depends on batch
        sizes at run time."""

        cpu = self.n_cpu_moe
        return {
            "granularity": "layer",
            "hot_layers_vram": self.hot_layers,
            "warm_layers_ram_gpu_compute": cpu if self.op_offload else 0,
            "cold_layers_cpu_compute": cpu,
            "warm_rule": (f"CPU-held layers run on the GPU when batch >= {OP_OFFLOAD_MIN_BATCH}"
                          if self.op_offload else "off (--no-op-offload)"),
            "cold_rule": (f"CPU-held layers run on the CPU when batch < {OP_OFFLOAD_MIN_BATCH}"
                          if self.op_offload else "CPU-held layers always run on the CPU"),
            "hot_expert_share_pct": round(100 * self.hot_layers / N_LAYERS, 1),
            "note": "llama.cpp pins whole layers (all 256 experts), not single experts",
        }

    def as_dict(self) -> dict:
        return {"quant": self.quant, "n_cpu_moe": self.n_cpu_moe, "ubatch": self.ubatch,
                "op_offload": self.op_offload, "threads": self.threads,
                "pinned_experts": [list(p) for p in self.pinned_experts], "flags": self.flags}

    @property
    def tag(self) -> str:
        return (f"{self.quant}-cpu{self.n_cpu_moe}-ub{self.ubatch}"
                f"-{'oo' if self.op_offload else 'nooo'}-t{self.threads}")


def baseline() -> SparkKnobs:
    """Reference try: agentic-floor quant, all experts on CPU, CPU-only compute."""

    return SparkKnobs(DEFAULT_QUANT, N_LAYERS, 512, False, DEFAULT_THREADS)


def grid(quants=STAGE_QUANTS, **kw) -> list[SparkKnobs]:
    """Back-compat alias for the planner's default search."""

    from wattnot.spark_open.planner import plan
    return plan(quants=quants, **kw).tries


def pin_plan(routing_counts, vram_slots):  # back-compat import path
    from wattnot.spark_open.placement import pin_plan as _p
    return _p(routing_counts, vram_slots)
