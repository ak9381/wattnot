"""Per-expert pinning loader for Muse Spark Open on vLLM.

llama.cpp pins whole layers (`--n-cpu-moe`, see knobs.py); this module is the
per-expert path for a patched vLLM (see patches/expert_pinning.patch). It:

1. classifies (layer, expert) pairs into HOT (resident in VRAM) vs COLD (pinned
   host RAM, copied on demand) from router frequency and a VRAM slot budget;
2. serializes that map to the JSON the patch reads via an env var, and returns
   the launch args a run would add;
3. offers a guarded `register()` that would install the patch at runtime but
   degrades to a clean status dict when vLLM is absent (as it is here).

Everything here is importable and testable with only `PYTHONPATH=src` -- no GPU,
no vLLM. The map it emits is SIMULATED/MOCK until a real router produces counts
and a real (patched) vLLM applies it; nothing in this repo applies it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from wattnot.spark_open.knobs import SparkKnobs
from wattnot.spark_open.placement import pin_plan

# The patch reads the map path from this env var; keep the two in lockstep.
ENV_PIN_MAP = "WATTNOT_EXPERT_PIN_MAP"
MAP_VERSION = 1
# Residency is SIMULATED here: no router counts and no patched vLLM in this repo.
MAP_LABEL = "SIMULATED"


@dataclass(frozen=True)
class PlacementMap:
    """Hot/cold split for per-expert pinning.

    `hot` are the (layer, expert) pairs kept resident in VRAM, picked as the
    top-`vram_slots` by routing count (ties broken by (layer, expert) so the map
    is deterministic). `cold` are the remaining observed pairs, which the patch
    stages from host RAM on demand.
    """

    hot: tuple[tuple[int, int], ...]
    cold: tuple[tuple[int, int], ...]
    vram_slots: int
    label: str = MAP_LABEL
    reason: str = ""

    def to_dict(self) -> dict:
        """JSON-ready form; the schema the vLLM patch consumes."""

        return {
            "version": MAP_VERSION,
            "label": self.label,
            "vram_slots": self.vram_slots,
            "reason": self.reason,
            "hot": [list(p) for p in self.hot],
            "cold": [list(p) for p in self.cold],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PlacementMap":
        """Inverse of `to_dict` (round-trips a serialized map)."""

        return cls(
            hot=tuple((int(a), int(b)) for a, b in d.get("hot", ())),
            cold=tuple((int(a), int(b)) for a, b in d.get("cold", ())),
            vram_slots=int(d.get("vram_slots", 0)),
            label=d.get("label", MAP_LABEL),
            reason=d.get("reason", ""),
        )


def build_placement_map(routing_counts: dict[tuple[int, int], int] | None,
                        vram_slots: int) -> PlacementMap:
    """Classify observed experts into HOT (VRAM) and COLD (host RAM).

    Reuses `pin_plan` from placement.py to choose the hot set (hottest experts
    by routing count, deterministic tie-break), then everything else observed is
    cold. With no counts there is nothing to pin per expert, so both sets are
    empty and the reason explains the fall-back to whole-layer placement.
    """

    plan = pin_plan(routing_counts, vram_slots)
    slots = max(0, vram_slots)
    if plan["mode"] != "expert" or not routing_counts:
        return PlacementMap(hot=(), cold=(), vram_slots=slots,
                            reason=plan["reason"])
    hot = tuple((int(a), int(b)) for a, b in plan["pinned"])
    hot_set = set(hot)
    cold = tuple(k for k in sorted(routing_counts, key=lambda k: (-routing_counts[k], k))
                 if k not in hot_set)
    return PlacementMap(hot=hot, cold=cold, vram_slots=slots,
                        reason=plan["reason"])


def map_from_knobs(knobs: SparkKnobs, vram_slots: int | None = None) -> PlacementMap:
    """Build a map straight from a SparkKnobs' `pinned_experts`.

    The knob already carries the chosen hot pairs, so they are treated as the
    hot set directly (no counts needed). `vram_slots` defaults to the number of
    pinned pairs. Cold is unknown from the knob alone, so it stays empty.
    """

    hot = tuple((int(a), int(b)) for a, b in knobs.pinned_experts)
    slots = len(hot) if vram_slots is None else max(0, vram_slots)
    reason = ("pinned experts from SparkKnobs" if hot else
              "no pinned experts on these knobs")
    return PlacementMap(hot=hot, cold=(), vram_slots=slots, reason=reason)


def write_placement_map(placement: PlacementMap, path: str | Path) -> Path:
    """Serialize the map to JSON on disk; returns the path written."""

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(placement.to_dict(), indent=2))
    return p


def read_placement_map(path: str | Path) -> PlacementMap:
    """Load a map written by `write_placement_map` (round-trip helper)."""

    return PlacementMap.from_dict(json.loads(Path(path).read_text()))


def launch_args(placement: PlacementMap, path: str | Path) -> list[str]:
    """Write the map and return the flags a (patched) vLLM run would add.

    The patch reads the file path from the env var, so the serving command does
    not change; we surface the setting as a `--env` pair for the launcher to put
    in the process environment. With nothing to pin, no flags are added.
    """

    if not placement.hot:
        return []
    written = write_placement_map(placement, path)
    return ["--env", f"{ENV_PIN_MAP}={written}"]


def map_env(placement: PlacementMap, path: str | Path) -> dict[str, str]:
    """The environment the patched vLLM needs (empty when nothing is pinned)."""

    if not placement.hot:
        return {}
    written = write_placement_map(placement, path)
    return {ENV_PIN_MAP: str(written)}


@dataclass
class LoaderStatus:
    """Result of `register()`: did the patch/plugin install, and why/why not."""

    installed: bool
    backend: str          # "vllm" | "none"
    label: str            # SIMULATED/MOCK/MEASURED-style tag
    reason: str
    details: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"installed": self.installed, "backend": self.backend,
                "label": self.label, "reason": self.reason, "details": self.details}


def register(placement: PlacementMap | None = None, *,
             map_path: str | Path | None = None,
             vllm_module=None) -> LoaderStatus:
    """Install the per-expert pinning plugin, guarded so it never crashes.

    vLLM is imported lazily (never at module top level). If it is absent -- as in
    this repo, with no GPU -- `register` returns a clean status dict instead of
    raising, so the caller can record that per-expert pinning was unavailable and
    fall back to whole-layer placement. `vllm_module` lets a test inject a fake
    vLLM to exercise the install path without the real package.
    """

    if vllm_module is None:
        try:
            import vllm as vllm_module  # noqa: PLC0415 - lazy, guarded on purpose
        except ImportError as e:
            return LoaderStatus(
                installed=False, backend="none", label=MAP_LABEL,
                reason=f"vLLM not installed ({e}); per-expert pinning unavailable, "
                       "fall back to llama.cpp whole-layer placement",
                details={"env_var": ENV_PIN_MAP})

    # vLLM (real or fake) is present: publish the map through the env var the
    # patch reads. We do not monkeypatch kernels here -- residency lives in the
    # patched fused MoE layer; the loader's job is to hand it the map.
    details: dict = {"env_var": ENV_PIN_MAP,
                     "vllm_version": getattr(vllm_module, "__version__", "unknown")}
    if placement is not None and placement.hot:
        path = map_path or os.environ.get(ENV_PIN_MAP) or "expert_pin_map.json"
        written = write_placement_map(placement, path)
        os.environ[ENV_PIN_MAP] = str(written)
        details["map_path"] = str(written)
        details["hot_experts"] = len(placement.hot)
    return LoaderStatus(
        installed=True, backend="vllm", label=MAP_LABEL,
        reason="vLLM present; per-expert pin map published via env var "
               "(patch applies residency; unverified in this repo)",
        details=details)
