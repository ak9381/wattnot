"""Where experts live. llama.cpp places whole layers; per-expert is a stub.

`LayerPlacement` is what runs today. `ExpertPlacement` is the interface for a
later colibri backend (github.com/JustVugg/colibri), which keeps single experts
in a VRAM cache. It is not wired up and raises NotImplementedError.
"""

from __future__ import annotations

from typing import Protocol

from wattnot.spark_open.knobs import N_LAYERS, SparkKnobs


class Placement(Protocol):
    granularity: str

    def args(self, knobs: SparkKnobs) -> list[str]: ...


class LayerPlacement:
    """llama.cpp: `--cpu-moe` (all layers) or `--n-cpu-moe N` (first N layers)."""

    granularity = "layer"

    def args(self, knobs: SparkKnobs) -> list[str]:
        if knobs.n_cpu_moe >= N_LAYERS:
            return ["--cpu-moe"]
        if knobs.n_cpu_moe == 0:
            return []
        return ["--n-cpu-moe", str(knobs.n_cpu_moe)]


class ExpertPlacement:
    """Stretch: pin the hottest (layer, expert) pairs by router frequency.

    Needs an engine that can hold single experts in VRAM (colibri's expert
    cache, or a llama.cpp patch that splits the stacked expert tensors). Not
    implemented; kept so the planner and receipts already have the shape.
    """

    granularity = "expert"

    def __init__(self, routing_counts: dict[tuple[int, int], int], vram_slots: int) -> None:
        self.plan = pin_plan(routing_counts, vram_slots)

    def args(self, knobs: SparkKnobs) -> list[str]:
        raise NotImplementedError("per-expert pinning needs colibri or a llama.cpp patch")


def pin_plan(routing_counts: dict[tuple[int, int], int] | None, vram_slots: int) -> dict:
    """Pick the hottest (layer, expert) pairs to keep in VRAM.

    With no counts (llama.cpp does not report per-expert routing) we fall back
    to the layer plan and say why.
    """

    if not routing_counts:
        return {"mode": "layer", "pinned": [],
                "reason": "no per-expert routing stats from llama.cpp; using --n-cpu-moe"}
    hot = sorted(routing_counts, key=lambda k: (-routing_counts[k], k))[:max(0, vram_slots)]
    return {"mode": "expert", "pinned": [list(k) for k in hot],
            "reason": "hottest experts by routing count (not yet applied by any engine here)"}
