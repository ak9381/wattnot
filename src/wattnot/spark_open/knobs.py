"""The two Spark Open knobs: quant level and VRAM expert budget.

Both need a model reload, so every try is "restart llama-server with these flags".
Quants are the ready-made Unsloth GGUFs; nothing is re-quantized here.
"""

from __future__ import annotations

from dataclasses import dataclass

REPO = "unsloth/Muse-Spark-Open-816B-A42B-GGUF"
N_LAYERS = 62  # model card: 62 layers, 256 experts, top-8


@dataclass(frozen=True)
class Quant:
    name: str
    approx_gb: int     # file size, ESTIMATE from the repo listing
    agentic_ok: bool   # Unsloth: below UD-IQ2_S, agentic tasks degrade
    note: str = ""


QUANTS: dict[str, Quant] = {
    "UD-IQ1_S": Quant("UD-IQ1_S", 176, False, "low end: chat holds up, agentic degrades"),
    "UD-IQ2_XXS": Quant("UD-IQ2_XXS", 220, False, "below the agentic floor"),
    "UD-IQ2_S": Quant("UD-IQ2_S", 260, True, "default; agentic floor"),
}
DEFAULT_QUANT = "UD-IQ2_S"
AGENTIC_FLOOR = "UD-IQ2_S"
# The side stage grid: 2 quants x 3 expert budgets = 6 tries.
STAGE_QUANTS = ("UD-IQ2_S", "UD-IQ1_S")
STAGE_BUDGETS = (N_LAYERS, 40, 20)  # n_cpu_moe: all experts on CPU, then more in VRAM


@dataclass(frozen=True)
class SparkKnobs:
    """`n_cpu_moe` = how many layers keep their experts in host RAM.

    62 puts every expert on the CPU (the same as `--override-tensor exps=CPU`);
    lower numbers pin more experts in VRAM, which is the VRAM expert budget.
    """

    quant: str = DEFAULT_QUANT
    n_cpu_moe: int = N_LAYERS
    # Later: pin the hottest experts in VRAM instead of whole layers. Empty today,
    # because llama.cpp does not expose per-expert routing counts. See pin_plan().
    pinned_experts: tuple[tuple[int, int], ...] = ()

    def __post_init__(self) -> None:
        if self.quant not in QUANTS:
            raise ValueError(f"quant must be one of {sorted(QUANTS)}")
        if not 0 <= self.n_cpu_moe <= N_LAYERS:
            raise ValueError(f"n_cpu_moe must be in 0..{N_LAYERS}")

    @property
    def flags(self) -> list[str]:
        """Warnings that travel with the try into its record."""

        q = QUANTS[self.quant]
        return [] if q.agentic_ok else [f"{q.name} is below the agentic floor ({AGENTIC_FLOOR})"]

    def as_dict(self) -> dict:
        return {"quant": self.quant, "n_cpu_moe": self.n_cpu_moe,
                "pinned_experts": [list(p) for p in self.pinned_experts], "flags": self.flags}


def grid(quants=STAGE_QUANTS, budgets=STAGE_BUDGETS) -> list[SparkKnobs]:
    return [SparkKnobs(q, b) for q in quants for b in budgets]


def pin_plan(routing_counts: dict[tuple[int, int], int] | None, vram_slots: int) -> dict:
    """Pick the hottest (layer, expert) pairs to keep in VRAM.

    This is a stub for a later version. llama.cpp keeps experts per layer
    (`--n-cpu-moe`) and has no per-expert routing counter we can read, so with
    no counts we return the layer-level plan and say why.
    """

    if not routing_counts:
        return {"mode": "layer", "pinned": [],
                "reason": "no per-expert routing stats from llama.cpp; using --n-cpu-moe"}
    hot = sorted(routing_counts, key=lambda k: (-routing_counts[k], k))[:max(0, vram_slots)]
    return {"mode": "expert", "pinned": [list(k) for k in hot],
            "reason": "hottest experts by routing count (not yet applied by llama-server)"}
