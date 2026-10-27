"""Optuna search over the power cap and vLLM knobs, warm-started by Muse Spark.

`propose()` asks Muse Spark for a few promising settings (numbers only: the
bounds, baseline Wh/request and p99 budget). `make_study()` enqueues them so
TPE tries them first, then explores. The p99 budget is a hard constraint:
an over-budget trial scores as infeasible.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass

import optuna

from wattnot.planner.spark import MODEL, SparkClient

optuna.logging.set_verbosity(optuna.logging.WARNING)


@dataclass(frozen=True)
class Bounds:
    cap_min_w: int = 400
    cap_max_w: int = 700
    cap_step_w: int = 25
    max_num_seqs: tuple[int, ...] = (64, 128, 256, 512)
    quantization: tuple[str, ...] = ("none", "fp8")

    def caps(self) -> list[int]:
        return list(range(self.cap_min_w, self.cap_max_w + 1, self.cap_step_w))

    def clamp(self, p: Mapping) -> dict:
        cap = int(p.get("power_cap_w", self.cap_max_w))
        cap = min(max(cap, self.cap_min_w), self.cap_max_w)
        cap = self.cap_min_w + round((cap - self.cap_min_w) / self.cap_step_w) * self.cap_step_w
        seqs = min(self.max_num_seqs, key=lambda s: abs(s - int(p.get("max_num_seqs", 256))))
        q = p.get("quantization") if p.get("quantization") in self.quantization else "none"
        return {"power_cap_w": cap, "max_num_seqs": seqs, "quantization": q}


@dataclass(frozen=True)
class Proposal:
    params: dict
    reason: str
    source: str  # "spark" or "fallback"


@dataclass(frozen=True)
class PlannerContext:
    bounds: Bounds
    base_wh_per_req: float
    base_p99_s: float
    p99_budget_s: float


PROPOSE_SYSTEM = (
    "You tune one GPU serving an LLM for lower energy per request within a p99 latency budget. "
    "Propose N distinct starting settings for a Bayesian search: power_cap_w within the bounds, "
    "max_num_seqs from the list, quantization from the list. Spread them out; one short reason "
    "each. JSON only."
)

PROPOSE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["proposals"],
    "properties": {"proposals": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["power_cap_w", "max_num_seqs", "quantization", "reason"],
        "properties": {"power_cap_w": {"type": "integer"}, "max_num_seqs": {"type": "integer"},
                       "quantization": {"type": "string"},
                       "reason": {"type": "string", "maxLength": 200}}}}},
}


def propose_body(ctx: PlannerContext, n: int = 4, model: str = MODEL) -> dict:
    b = ctx.bounds
    summary = {"n": n, "cap_w": {"min": b.cap_min_w, "max": b.cap_max_w, "step": b.cap_step_w},
               "max_num_seqs": list(b.max_num_seqs), "quantization": list(b.quantization),
               "baseline": {"wh_per_req": ctx.base_wh_per_req, "p99_s": ctx.base_p99_s},
               "p99_budget_s": ctx.p99_budget_s}
    return {"model": model, "reasoning_effort": "minimal", "max_tokens": 800,
            "messages": [{"role": "system", "content": PROPOSE_SYSTEM},
                         {"role": "user", "content": json.dumps(summary, separators=(",", ":"))}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "Proposals", "schema": PROPOSE_SCHEMA, "strict": True}}}


def fallback_proposals(ctx: PlannerContext, n: int = 4) -> list[Proposal]:
    b = ctx.bounds
    mid = b.cap_min_w + (b.cap_max_w - b.cap_min_w) // 2
    picks = [{"power_cap_w": b.cap_max_w, "max_num_seqs": 256, "quantization": "fp8"},
             {"power_cap_w": mid, "max_num_seqs": 256, "quantization": "none"},
             {"power_cap_w": mid, "max_num_seqs": 512, "quantization": "fp8"},
             {"power_cap_w": b.cap_min_w, "max_num_seqs": 128, "quantization": "none"}]
    return [Proposal(b.clamp(p), "fixed spread", "fallback") for p in picks[:n]]


def propose(ctx: PlannerContext, n: int = 4, *, transport: Callable[[dict], dict] | None = None,
            model: str = MODEL) -> tuple[list[Proposal], dict]:
    """Return (proposals, usage). Any failure falls back to a fixed spread."""

    send = transport or SparkClient(model=model, purpose="propose-start")
    try:
        raw = send(propose_body(ctx, n, model))
        data = json.loads(raw["choices"][0]["message"]["content"])
        seen, out = set(), []
        for p in data["proposals"]:
            params = ctx.bounds.clamp(p)
            key = tuple(params.values())
            if key not in seen:
                seen.add(key)
                out.append(Proposal(params, str(p.get("reason", ""))[:200], "spark"))
        if not out:
            raise ValueError("no proposals")
        u = raw.get("usage") or {}
        return out[:n], {"calls": 1, "input": int(u.get("prompt_tokens", 0)),
                         "output": int(u.get("completion_tokens", 0))}
    except Exception:
        return fallback_proposals(ctx, n), {"calls": 1,
                                            "input": 0, "output": 0, "fallback": True}


def make_study(seed_trials: Sequence[Proposal], bounds: Bounds, *, seed: int = 7) -> optuna.Study:
    study = optuna.create_study(direction="minimize",
                                sampler=optuna.samplers.TPESampler(seed=seed,
                                                                   n_startup_trials=4))
    for p in seed_trials:
        study.enqueue_trial(dict(p.params))
    return study


def suggest(trial: optuna.Trial, bounds: Bounds) -> dict:
    return {"power_cap_w": trial.suggest_int("power_cap_w", bounds.cap_min_w, bounds.cap_max_w,
                                             step=bounds.cap_step_w),
            "max_num_seqs": trial.suggest_categorical("max_num_seqs", list(bounds.max_num_seqs)),
            "quantization": trial.suggest_categorical("quantization", list(bounds.quantization))}


def proposals_trace(props: Sequence[Proposal]) -> list[dict]:
    return [{"trial_no": i + 1, "source": p.source, "model": MODEL if p.source == "spark"
             else None, "params": p.params, "reason": p.reason} for i, p in enumerate(props)]


__all__ = ["Bounds", "PlannerContext", "Proposal", "asdict", "fallback_proposals",
           "make_study", "propose", "proposals_trace", "suggest"]
