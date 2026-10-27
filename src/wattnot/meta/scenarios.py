"""Toy response surfaces for the agent's next-knob decisions (labelled SIMULATED).

They are shaped from earlier measurements (power-cap curve, expert-budget slice test,
hosted effort runs) so the decisions are sensible, but the numbers they return are not
measurements and are never put in a receipt. Each has the same shape the live planner
sees: knobs with allowed values, limits, and a list of trials with their gate results.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

LABEL = "SIMULATED"


@dataclass(frozen=True)
class Scenario:
    name: str
    goal: str
    knobs: dict
    limits: dict
    start: dict
    surface: Callable[[Mapping], dict]  # settings -> {saving_pct, p99_delta_pct, quality, ...}

    def prompt_view(self) -> dict:
        return {"name": self.name, "goal": self.goal, "knobs": self.knobs, "limits": self.limits}

    def run(self, settings: Mapping) -> dict:
        m = self.surface(settings)
        lim = self.limits
        m["passed"] = (m["p99_delta_pct"] <= lim["max_p99_increase_pct"]
                       and m["quality_pct"] >= lim["min_quality_pct"])
        return m


def _gpu(s: Mapping) -> dict:
    cap = s["power_cap_w"]
    wh = 0.048 * (0.55 + 0.45 * (cap / 700) ** 1.5)
    p99 = 1000 * (700 / cap) ** 1.1
    if s["max_num_seqs"] == 512:
        wh *= 0.94
        p99 *= 1.04
    elif s["max_num_seqs"] == 128:
        wh *= 1.05
        p99 *= 0.97
    if s["enable_prefix_caching"]:
        wh *= 0.97
        p99 *= 0.98
    q = 100.0
    if s["quantization"] == "fp8":
        wh *= 0.9
        p99 *= 0.95
        q = 99.4
    return {"saving_pct": round(100 * (1 - wh / 0.048), 1),
            "p99_delta_pct": round(p99 / 10 - 100, 1), "quality_pct": q, "label": LABEL}


def _moe(s: Mapping) -> dict:
    keep, adaptive = s["expert_keep_fraction"], s["adaptive_top_k"] == "on"
    agree = {1.0: 100.0, 0.85: 91.0, 0.7: 82.0, 0.5: 69.0}[keep]
    flops = 0.0
    if adaptive:
        flops, agree = 15.0, agree * 0.92
    return {"saving_pct": flops, "memory_saving_pct": round(100 * (1 - keep), 1),
            "p99_delta_pct": 0.0 if keep == 1.0 else -1.0, "quality_pct": round(agree, 1),
            "label": LABEL}


def _hosted(s: Mapping) -> dict:
    tokens = {"high": 1.0, "medium": 0.55, "low": 0.28, "minimal": 0.14}[s["reasoning_effort"]]
    tokens *= {1.0: 1.0, 0.75: 0.9, 0.5: 0.8}[s["max_tokens_scale"]]
    cost = 0.2 + 0.8 * tokens
    if s["shared_prefix_first"]:
        cost *= 0.88
    q = {"high": 100.0, "medium": 99.6, "low": 97.0, "minimal": 90.0}[s["reasoning_effort"]]
    q -= {1.0: 0.0, 0.75: 0.5, 0.5: 4.0}[s["max_tokens_scale"]]
    lat = tokens * 0.8 + 0.2 - 1.0
    return {"saving_pct": round(100 * (1 - cost), 1), "p99_delta_pct": round(100 * lat, 1),
            "quality_pct": round(q, 1), "label": LABEL}


SCENARIOS = {
    "gpu-power": Scenario(
        "gpu-power", "lower Wh per request on one H100 serving a chat model",
        {"power_cap_w": [700, 600, 550, 500, 450, 400], "max_num_seqs": [128, 256, 512],
         "enable_prefix_caching": [True, False], "quantization": ["none", "fp8"]},
        {"max_p99_increase_pct": 10, "min_quality_pct": 99},
        {"power_cap_w": 700, "max_num_seqs": 256, "enable_prefix_caching": False,
         "quantization": "none"}, _gpu),
    "moe-routing": Scenario(
        "moe-routing", "cut expert compute or memory in a mixture-of-experts model; quality is "
        "next-token agreement with the full model on our workload",
        {"expert_keep_fraction": [1.0, 0.85, 0.7, 0.5], "adaptive_top_k": ["off", "on"]},
        {"max_p99_increase_pct": 10, "min_quality_pct": 90},
        {"expert_keep_fraction": 1.0, "adaptive_top_k": "off"}, _moe),
    "hosted-effort": Scenario(
        "hosted-effort", "cut cost per request on a hosted reasoning model; saving is vs the "
        "default `high` effort",
        {"reasoning_effort": ["high", "medium", "low", "minimal"],
         "max_tokens_scale": [1.0, 0.75, 0.5], "shared_prefix_first": [False, True]},
        {"max_p99_increase_pct": 10, "min_quality_pct": 98},
        {"reasoning_effort": "high", "max_tokens_scale": 1.0, "shared_prefix_first": False},
        _hosted),
}
