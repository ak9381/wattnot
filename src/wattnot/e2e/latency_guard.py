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


RERUN_ENV = "WATTNOT_STEPUP_RERUN_FACTOR"


def rerun_factor(default: float = 0.0) -> float:
    raw = os.environ.get(RERUN_ENV, "").strip()
    try:
        return max(0.0, float(raw)) if raw else default
    except ValueError:
        return default


def allow_step_up(low_wall_s: float, limit: float | None = None, rerun: float | None = None) -> bool:
    """True when a step-up is allowed after a low attempt that took `low_wall_s` seconds.

    `rerun` projects the step-up: the medium rerun is assumed to take `rerun` x the low time, so the
    step-up is allowed only when low + projected rerun fits the limit. 0 keeps the old rule (low
    alone vs the limit). Baseten r03 own-set: all 8 step-ups were 10-step agent items whose medium
    rerun took about as long as the low try (low 16-24 s, rerun 19-36 s), so the old rule let them
    all through and the with-arm p95 went 23 s -> 42 s.
    """
    lim = max_low_s() if limit is None else limit
    f = rerun_factor() if rerun is None else max(0.0, float(rerun))
    return lim <= 0 or float(low_wall_s or 0.0) * (1.0 + f) <= lim


P99_LIMIT = 1.10


def pctl(xs, q):
    """Same nearest-rank percentile as the round scripts."""
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def _reason(a: dict) -> str:
    tries = a.get("tries") or []
    if len(tries) > 1:
        return "step-up " + " -> ".join(map(str, tries))
    if a.get("stepup_skipped"):
        return "step-up skipped (latency guard)"
    return "single attempt at " + (str(tries[0]) if tries else "unknown")


def latency_verdict(without: list[dict], with_: list[dict], limit: float = P99_LIMIT, max_drop: int = 2) -> dict:
    """Per-item latency verdict: "pass", "pass_with_latency_note" or "fail".

    The strict check is unchanged: with-arm item p99 within `limit` of the without arm. If it misses,
    the verdict is "pass_with_latency_note" only when (a) the with-arm p95 is within the limit, and
    (b) dropping the slowest 1..`max_drop` with-arm items (each above the without-arm p99) brings
    the with-arm p99 within the limit. Those items are listed with both arms' wall_s.
    """
    wo = [float(a.get("wall_s") or 0) for a in without]
    wi = [float(a.get("wall_s") or 0) for a in with_]
    p99o, p99i, p95o, p95i = pctl(wo, .99), pctl(wi, .99), pctl(wo, .95), pctl(wi, .95)
    out = {"rule": f"with-arm item p99 within +{round((limit - 1) * 100)}% of without; note allowed if p95 passes and "
                   f"dropping <= {max_drop} with-arm items above the without p99 fixes p99",
           "p99_without_s": p99o, "p99_with_s": p99i, "p95_without_s": p95o, "p95_with_s": p95i,
           "items": [], "label": "MEASURED"}
    if p99o is None or p99i is None:
        out.update(verdict="fail", strict_ok=False, p95_ok=None, why="no answers")
        return out
    strict = p99i <= limit * p99o
    p95_ok = p95i <= limit * p95o
    out.update(strict_ok=strict, p95_ok=p95_ok)
    if strict:
        out["verdict"] = "pass"
        return out
    above = sorted((a for a in with_ if float(a.get("wall_s") or 0) > p99o),
                   key=lambda a: -float(a.get("wall_s") or 0))
    wo_by = {(a.get("set"), a.get("id")): a for a in without}
    if p95_ok and above:
        for k in range(1, min(max_drop, len(above)) + 1):
            drop = {id(a) for a in above[:k]}
            p99_rest = pctl([float(a.get("wall_s") or 0) for a in with_ if id(a) not in drop], .99)
            if p99_rest is not None and p99_rest <= limit * p99o:
                out.update(verdict="pass_with_latency_note", p99_with_after_drop_s=p99_rest)
                out["items"] = [{"id": a.get("id"), "set": a.get("set"), "with_wall_s": a.get("wall_s"),
                                 "without_wall_s": (wo_by.get((a.get("set"), a.get("id"))) or {}).get("wall_s"),
                                 "reason": _reason(a)} for a in above[:k]]
                return out
    out["verdict"] = "fail"
    out["items_above_without_p99"] = len(above)
    return out


def load_answers(path) -> list[dict]:
    import json
    from pathlib import Path
    p = Path(path)
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []
