"""Noise gate: is the energy saving real, or just run-to-run noise?

Welch's one-sided t-test on per-rep Wh/request (baseline mean greater than
candidate mean), plus a seeded bootstrap 95% CI of the saving in percent.
A saving is billable only when p < alpha and the CI lower bound is above 0.
We bill the lower bound, never the mean. Pure Python, no SciPy needed.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

from wattnot.gates.result import GateResult


@dataclass(frozen=True)
class Welch:
    t: float
    df: float
    p_value: float  # one-sided, H1: mean(base) > mean(cand)


def _mean_var(x: Sequence[float]) -> tuple[float, float]:
    n = len(x)
    if n < 2:
        raise ValueError("need at least 2 values for mean and variance")
    m = sum(x) / n
    return m, sum((v - m) ** 2 for v in x) / (n - 1)


def _betacf(a: float, b: float, x: float) -> float:
    # Continued fraction for the incomplete beta (Numerical Recipes, Lentz).
    tiny, qab, qap, qam = 1e-300, a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > tiny else tiny)
        c = 1 + aa / c if abs(1 + aa / c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > tiny else tiny)
        c = 1 + aa / c if abs(1 + aa / c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-15:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) \
        + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lbt) * _betacf(a, b, x) / a
    return 1 - math.exp(lbt) * _betacf(b, a, 1 - x) / b


def t_sf(t: float, df: float) -> float:
    """P(T > t) for Student's t with `df` degrees of freedom."""

    tail = 0.5 * _betainc(df / 2, 0.5, df / (df + t * t))
    return tail if t >= 0 else 1 - tail


def welch(base: Sequence[float], cand: Sequence[float]) -> Welch:
    if len(base) < 2 or len(cand) < 2:
        raise ValueError("need at least 2 reps per arm")
    m1, v1 = _mean_var(base)
    m2, v2 = _mean_var(cand)
    s1, s2 = v1 / len(base), v2 / len(cand)
    se2 = s1 + s2
    if se2 == 0:
        if m1 == m2:
            return Welch(0.0, float(len(base) + len(cand) - 2), 0.5)
        t = math.inf if m1 > m2 else -math.inf
        return Welch(t, float(len(base) + len(cand) - 2), 0.0 if m1 > m2 else 1.0)
    t = (m1 - m2) / math.sqrt(se2)
    df = se2 ** 2 / (s1 ** 2 / (len(base) - 1) + s2 ** 2 / (len(cand) - 1)) if s1 or s2 else 0
    return Welch(t, df, t_sf(t, df))


def _saving_pct(base: Sequence[float], cand: Sequence[float]) -> float:
    if not base or not cand:
        raise ValueError("both arms need at least one rep")
    mb = sum(base) / len(base)
    if mb == 0:
        raise ValueError("baseline mean is zero; saving percent is undefined")
    return 100 * (mb - sum(cand) / len(cand)) / mb


def bootstrap_saving_ci(base: Sequence[float], cand: Sequence[float], *, n_boot: int = 10_000,
                        seed: int = 7, level: float = 0.95) -> tuple[float, float]:
    """Percentile bootstrap CI of the saving percent, resampling each arm."""

    rng = random.Random(seed)
    nb, nc = len(base), len(cand)
    stats = sorted(_saving_pct([base[rng.randrange(nb)] for _ in range(nb)],
                               [cand[rng.randrange(nc)] for _ in range(nc)])
                   for _ in range(n_boot))
    lo_i = int(math.floor((1 - level) / 2 * (n_boot - 1)))
    hi_i = int(math.ceil((1 + level) / 2 * (n_boot - 1)))
    return round(stats[lo_i], 4), round(stats[hi_i], 4)


def noise_gate(base_wh: Sequence[float], cand_wh: Sequence[float], alpha: float = 0.05,
               n_boot: int = 10_000, seed: int = 7) -> GateResult:
    w = welch(base_wh, cand_wh)
    lo, hi = bootstrap_saving_ci(base_wh, cand_wh, n_boot=n_boot, seed=seed)
    saving = round(_saving_pct(base_wh, cand_wh), 4)
    billable = w.p_value < alpha and lo > 0
    detail = {"t": round(w.t, 6) if math.isfinite(w.t) else 1e9, "df": round(w.df, 6),
              "p_value": w.p_value, "alpha": alpha, "saving_pct": saving, "ci_low": lo,
              "ci_high": hi, "n_boot": n_boot, "seed": seed,
              "grade": "billable" if billable else "NOISE",
              "billed_saving_pct": lo if billable else 0}
    reason = (f"saving {saving}% (95% CI {lo} to {hi}), p={w.p_value:.3g}; billing {lo}%"
              if billable else f"saving {saving}% is within noise (p={w.p_value:.3g}, "
              f"CI low {lo}); not billed")
    return GateResult("noise", billable, reason, detail)
