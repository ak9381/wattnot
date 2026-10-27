from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WasteConfig:
    """Limits for one agent run. All values are plain ints, floats or strings."""

    max_steps: int = 12
    max_total_tokens: int = 60000
    repeat_limit: int = 2
    noop_limit: int = 2
    cycle_max_len: int = 4
    reasoning_floor: int = 400
    reasoning_ratio: float = 4.0
    reasoning_hard_cap: int = 3000
    max_nudges: int = 1
    inject_done: bool = True
    done_tool: str = "done"

    @classmethod
    def from_dict(cls, d: dict) -> WasteConfig:
        """Build from a dict. Unknown keys raise ValueError."""
        known = set(cls.__dataclass_fields__)
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown WasteConfig keys: {sorted(unknown)}")
        return cls(**{k: v for k, v in d.items() if k in known})
