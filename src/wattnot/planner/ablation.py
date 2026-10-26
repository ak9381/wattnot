"""Cold vs warm vs grid search on the same objective, with time and Wh budgets.

Each arm gets the same trial budget. For each arm we record when it first
reached the target (within `tol` of the best feasible point found by an
exhaustive grid): trials_to_best, seconds_to_best and wh_to_best (energy
spent running those trials). The synthetic objective is a stand-in for a
GPU and is labelled ESTIMATE.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import optuna

from wattnot.planner.optuna_study import Bounds, Proposal, make_study, suggest


@dataclass(frozen=True)
class Outcome:
    wh_per_req: float
    p99_s: float
    seconds: float      # wall time to run the trial
    wh_spent: float     # energy used by the trial itself


Objective = Callable[[dict], Outcome]


def synthetic_objective(n_requests: int = 200, base_wh: float = 0.048,
                        base_p99_s: float = 1.0) -> Objective:
    """A smooth toy GPU: lower cap and fp8 save energy, low caps and big batches add p99."""

    def f(p: dict) -> Outcome:
        cap = p["power_cap_w"] / 700
        q = 0.86 if p["quantization"] == "fp8" else 1.0
        seqs = p["max_num_seqs"]
        batch = {64: 1.12, 128: 1.04, 256: 1.0, 512: 0.97}[seqs]
        wh = base_wh * q * batch * (0.55 + 0.45 * cap + 0.25 * max(0, 0.75 - cap) ** 2 * 4)
        p99 = base_p99_s * (1 / cap) ** 1.1 * (0.92 if q < 1 else 1) * \
            {64: 0.9, 128: 0.95, 256: 1.0, 512: 1.12}[seqs]
        seconds = 120 * (1 / cap) ** 0.8
        return Outcome(round(wh, 7), round(p99, 4), round(seconds, 2),
                       round(wh * n_requests * 6, 4))  # 5 reps + warm-up
    return f


@dataclass
class ArmResult:
    arm: str
    trials: list[dict] = field(default_factory=list)
    best_wh: float | None = None
    trials_to_best: int | None = None
    seconds_to_best: float | None = None
    wh_to_best: float | None = None

    def summary(self) -> dict:
        return {"arm": self.arm, "n_trials": len(self.trials), "best_wh_per_req": self.best_wh,
                "trials_to_best": self.trials_to_best, "seconds_to_best": self.seconds_to_best,
                "wh_to_best": self.wh_to_best}


@dataclass
class AblationReport:
    target_wh: float
    tol: float
    budget_trials: int
    arms: dict[str, ArmResult]
    spark: dict
    label: str = "ESTIMATE"

    def to_dict(self) -> dict:
        return {"label": self.label, "target_wh_per_req": self.target_wh, "tol": self.tol,
                "budget_trials": self.budget_trials, "spark": self.spark,
                "arms": [a.summary() for a in self.arms.values()]}


def _grid(bounds: Bounds) -> list[dict]:
    # coarse grid order: caps high to low, then knobs (what a person would sweep)
    return [{"power_cap_w": c, "max_num_seqs": s, "quantization": q}
            for c, q, s in itertools.product(sorted(bounds.caps(), reverse=True),
                                             bounds.quantization, bounds.max_num_seqs)]


def _feasible_best(obj: Objective, bounds: Bounds, budget_s: float) -> float:
    vals = [o.wh_per_req for o in map(obj, _grid(bounds)) if o.p99_s <= budget_s]
    return min(vals)


def _record(arm: ArmResult, p: dict, o: Outcome, budget_s: float, target: float,
            tol: float) -> None:
    ok = o.p99_s <= budget_s
    arm.trials.append({**p, "wh_per_req": o.wh_per_req, "p99_s": o.p99_s, "feasible": ok,
                       "_s": o.seconds, "_wh": o.wh_spent})
    if ok and (arm.best_wh is None or o.wh_per_req < arm.best_wh):
        arm.best_wh = o.wh_per_req
    if arm.trials_to_best is None and ok and o.wh_per_req <= target * (1 + tol):
        arm.trials_to_best = len(arm.trials)
        arm.seconds_to_best = round(sum(t["_s"] for t in arm.trials), 2)
        arm.wh_to_best = round(sum(t["_wh"] for t in arm.trials), 4)


def _run_optuna(name: str, seeds: Sequence[Proposal], obj: Objective, bounds: Bounds,
                budget: int, budget_s: float, target: float, tol: float, seed: int) -> ArmResult:
    arm = ArmResult(name)
    study = make_study(seeds, bounds, seed=seed)

    def fn(trial: optuna.Trial) -> float:
        p = suggest(trial, bounds)
        o = obj(p)
        _record(arm, p, o, budget_s, target, tol)
        return o.wh_per_req if o.p99_s <= budget_s else o.wh_per_req * 10

    study.optimize(fn, n_trials=budget)
    return arm


def ablation(budget_trials: int, arms: Sequence[str] = ("cold", "warm", "grid"), *,
             proposals: Sequence[Proposal] = (), spark: dict | None = None,
             objective: Objective | None = None, bounds: Bounds | None = None,
             p99_budget_s: float = 1.3, tol: float = 0.01, seed: int = 7) -> AblationReport:
    bounds = bounds or Bounds()
    obj = objective or synthetic_objective()
    target = _feasible_best(obj, bounds, p99_budget_s)
    out: dict[str, ArmResult] = {}
    for name in arms:
        if name == "cold":
            out[name] = _run_optuna("cold", (), obj, bounds, budget_trials, p99_budget_s,
                                    target, tol, seed)
        elif name == "warm":
            out[name] = _run_optuna("warm", proposals, obj, bounds, budget_trials,
                                    p99_budget_s, target, tol, seed)
        elif name == "grid":
            arm = ArmResult("grid")
            for p in _grid(bounds)[:budget_trials]:
                _record(arm, p, obj(p), p99_budget_s, target, tol)
            out[name] = arm
        else:
            raise ValueError(f"unknown arm {name!r}")
    for a in out.values():
        for t in a.trials:
            t.pop("_s", None)
            t.pop("_wh", None)
    return AblationReport(target, tol, budget_trials, out, spark or {"calls": 0})
