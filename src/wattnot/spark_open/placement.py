"""Where experts live. llama.cpp places whole layers; per-expert uses vLLM.

`LayerPlacement` is what runs today on llama.cpp. `ExpertPlacement` is the
per-expert path: it keeps the hottest (layer, expert) pairs resident in VRAM
and offloads the rest to host RAM, via a patched vLLM fused MoE layer (see
patches/expert_pinning.patch, applied by expert_pin.register()). Its `.args()`
return the launch flags that publish the placement map to that patch. The
residency itself is SIMULATED here -- no GPU, no vLLM in this repo.
"""

from __future__ import annotations

from pathlib import Path
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
    """Pin the hottest (layer, expert) pairs by router frequency, in VRAM.

    Needs an engine that can hold single experts in VRAM; here that is a patched
    vLLM fused MoE layer (patches/expert_pinning.patch). `__init__` picks the hot
    set from routing counts (via `pin_plan`); `args` serialize the hot/cold map
    and return the launch flags that hand it to the patch. `map_path` is where
    the serialized JSON is written (defaults next to the cwd).
    """

    granularity = "expert"

    def __init__(self, routing_counts: dict[tuple[int, int], int], vram_slots: int,
                 *, map_path: str | Path = "expert_pin_map.json") -> None:
        self.routing_counts = routing_counts
        self.vram_slots = vram_slots
        self.map_path = Path(map_path)
        self.plan = pin_plan(routing_counts, vram_slots)

    def args(self, knobs: SparkKnobs) -> list[str]:
        """Launch flags that publish the per-expert placement map to vLLM.

        Hot experts come from the routing counts; if the knobs carry explicit
        `pinned_experts` and no counts were given, those are used instead. With
        nothing to pin (no counts, no pinned_experts) this returns no flags, so
        the behaviour degrades cleanly to whole-layer placement upstream.
        """

        from wattnot.spark_open.expert_pin import (
            build_placement_map,
            launch_args,
            map_from_knobs,
        )

        if self.routing_counts:
            placement = build_placement_map(self.routing_counts, self.vram_slots)
        else:
            placement = map_from_knobs(knobs, self.vram_slots)
        return launch_args(placement, self.map_path)


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
