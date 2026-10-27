"""Small statistics for paired blocks. Pure Python."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

# two-sided 95% Student t critical values by degrees of freedom
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
       9: 2.262, 10: 2.228}


def t95(df: int) -> float:
    return T95.get(df, 2.0 if df > 10 else float("nan"))


def mean_sd(xs: Sequence[float]) -> tuple[float, float]:
    if not xs:
        return float("nan"), float("nan")
    return statistics.fmean(xs), (statistics.stdev(xs) if len(xs) > 1 else 0.0)


def ci95(xs: Sequence[float]) -> tuple[float, float]:
    """Mean and the half-width of its 95% confidence interval."""
    m, sd = mean_sd(xs)
    if len(xs) < 2:
        return m, float("nan")
    return m, t95(len(xs) - 1) * sd / math.sqrt(len(xs))


def pct_change(new: float, old: float) -> float:
    return (new - old) / old * 100.0 if old else float("nan")


def percentile(values: Sequence[float], q: float) -> float:
    data = sorted(values)
    if not data:
        return float("nan")
    pos = (len(data) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return data[lo] + (data[hi] - data[lo]) * (pos - lo)
