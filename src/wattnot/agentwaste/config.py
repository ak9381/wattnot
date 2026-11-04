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
    # Lean nudge (gateway mode): on a detected loop, drop the repeated tool turns that returned the
    # same result, add a one-line note, and add NO done tool, so the nudged request is never longer
    # than stock. The full nudge (note + done tool) cost +51 prompt tokens per agent call on the
    # 4 Nov H100 Agent 60 run (745 vs 694, MEASURED).
    lean_nudge: bool = False
    # Second-layer detectors, off (0) by default so the first benchmark stays reproducible.
    error_limit: int = 0       # a tool that fails this many times in a row
    empty_limit: int = 0       # a tool that comes back empty this many times in a row
    low_gain_window: int = 0   # calls in a row that add almost no new words
    low_gain_max_new: float = 0.1
    paraphrase_limit: int = 0  # text-only replies in a row that say the same thing

    @classmethod
    def second_layer(cls, max_steps: int = 12, **kw) -> WasteConfig:
        """The preset for running behind a harness that has its own stop rules."""
        base = {"max_steps": max_steps, "error_limit": 3, "empty_limit": 4,
                "low_gain_window": 5, "low_gain_max_new": 0.1, "paraphrase_limit": 2}
        return cls(**{**base, **kw})

    @classmethod
    def from_dict(cls, d: dict) -> WasteConfig:
        """Build from a dict. Unknown keys raise ValueError."""
        known = set(cls.__dataclass_fields__)
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown WasteConfig keys: {sorted(unknown)}")
        return cls(**{k: v for k, v in d.items() if k in known})
