"""Latency guard for the cheapest-first router.

A step-up from low to the thinking tier reruns the whole item, so its time adds on top of the
low attempt. In r04f the two slowest items were both step-ups (low ~39 s and ~73 s, then the
full thinking rerun), which pushed the item p99 18% over the without-WattNot arm.

Rule: if the low attempt already took longer than the guard, keep the low answer and do not step
up. The default (30 s) comes from replaying r04f; 0 turns the guard off.
"""
from __future__ import annotations

import os

DEFAULT_MAX_LOW_S = 30.0
ENV = "WATTNOT_STEPUP_MAX_LOW_S"


def max_low_s() -> float:
    raw = os.environ.get(ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_LOW_S
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_MAX_LOW_S


def allow_step_up(low_wall_s: float, limit: float | None = None) -> bool:
    """True when a step-up is allowed after a low attempt that took `low_wall_s` seconds."""
    lim = max_low_s() if limit is None else limit
    return lim <= 0 or float(low_wall_s or 0.0) <= lim
