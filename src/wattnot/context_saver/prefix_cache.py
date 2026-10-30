"""Prefix-caching A/B: vLLM ``enable_prefix_caching`` off vs on, on a repeated-prefix workload.

When many requests share a long prefix (a system prompt plus repo context), vLLM can keep that
prefix's KV cache and skip recomputing it. This module measures what that buys: joules per correct
answer, prefix-cache hit rate (from vLLM's Prometheus metrics), time to first token (TTFT), p99 and
the quality gate, with arms off and on and n>=3 repeats.

Everything here is pure: the live CLI (``scripts/prefix_cache_ab.py``) feeds measured per-rep rows
in, and ``mock_ab`` builds a deterministic set for CPU rehearsals. Energy is an ESTIMATE unless the
caller passes MEASURED rows; cache hit rate and latency come straight from vLLM (MEASURED live).
"""

from __future__ import annotations

import re
import statistics as st
from collections.abc import Sequence

SPEC = "wattnot.prefix-cache-ab/1"
P99_LIMIT_PCT = 10.0
QUALITY_DROP_PTS = 1.0

_QUERIES = re.compile(r"^vllm:prefix_cache_queries_total(?:\{[^}]*\})?\s+([0-9.eE+]+)", re.M)
_HITS = re.compile(r"^vllm:prefix_cache_hits_total(?:\{[^}]*\})?\s+([0-9.eE+]+)", re.M)
_GAUGE = re.compile(r"^vllm:gpu_prefix_cache_hit_rate(?:\{[^}]*\})?\s+([0-9.eE+]+)", re.M)


def hit_rate_from_metrics(before_text: str, after_text: str) -> float | None:
    """Prefix-cache hit rate over a window: delta hits / delta queries from vLLM counters.

    Falls back to the ``gpu_prefix_cache_hit_rate`` gauge in ``after_text`` when the counters are
    absent. Returns a fraction in [0, 1], or None if neither metric is present.
    """
    def _sum(pat, text):
        vals = [float(x) for x in pat.findall(text)]
        return sum(vals) if vals else None
    def _clamp(x: float) -> float:
        return round(min(1.0, max(0.0, x)), 4)   # honour the documented [0, 1] contract
    q0, q1 = _sum(_QUERIES, before_text), _sum(_QUERIES, after_text)
    h0, h1 = _sum(_HITS, before_text), _sum(_HITS, after_text)
    if q0 is not None and q1 is not None and h0 is not None and h1 is not None and q1 - q0 > 0:
        # a counter reset/wrap can push the delta outside [0, 1]; clamp rather than report a
        # negative or >100% hit rate.
        return _clamp((h1 - h0) / (q1 - q0))
    g = _sum(_GAUGE, after_text)
    return _clamp(g) if g is not None else None


def _agg(rows: Sequence[dict], key: str) -> dict:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return {"mean": round(st.mean(vals), 4) if vals else None,
            "sd": round(st.stdev(vals), 4) if len(vals) > 1 else None, "n": len(vals)}


def arm_summary(name: str, rows: Sequence[dict], label: str) -> dict:
    """Aggregate per-rep rows (j_per_req, pass_pct, ttft_ms, p99_ms, cache_hit_rate) for one arm."""
    out = {"arm": name, "reps": len(rows), "label": label}
    for key in ("j_per_req", "pass_pct", "ttft_ms", "p99_ms", "cache_hit_rate"):
        out[key] = _agg(rows, key)
    jpr = out["j_per_req"]["mean"]
    passm = out["pass_pct"]["mean"]
    out["j_per_correct"] = round(jpr / (passm / 100), 4) if jpr and passm else None
    return out


def _change_pct(a, b):
    return round(100 * (b - a) / a, 2) if a else None


def evaluate_ab(off: dict, on: dict, *, p99_limit_pct: float = P99_LIMIT_PCT,
                quality_drop_pts: float = QUALITY_DROP_PTS) -> dict:
    """Gate the 'on' arm against 'off' and report the headline change in J per correct answer."""
    off_pass = off["pass_pct"]["mean"] or 0.0
    on_pass = on["pass_pct"]["mean"] or 0.0
    drop = round(off_pass - on_pass, 4)
    p99_change = _change_pct(off["p99_ms"]["mean"], on["p99_ms"]["mean"])
    quality_ok = drop <= quality_drop_pts
    p99_ok = p99_change is None or p99_change <= p99_limit_pct
    failed = []
    if not quality_ok:
        failed.append(f"pass rate dropped {drop:g} pts (> {quality_drop_pts:g})")
    if not p99_ok:
        failed.append(f"p99 rose {p99_change:g}% (> {p99_limit_pct:g}%)")
    if off_pass <= 0 or on_pass <= 0:   # both arms broken would read as "no drop" = a pass
        failed.append("0% graded pass rate on an arm: nothing to compare")
    return {
        "spec": SPEC,
        "lever": "enable_prefix_caching",
        "arms": {"off": off, "on": on},
        "headline": {
            "j_per_correct_change_pct": _change_pct(off["j_per_correct"], on["j_per_correct"]),
            "ttft_change_pct": _change_pct(off["ttft_ms"]["mean"], on["ttft_ms"]["mean"]),
            "p99_change_pct": p99_change,
            "cache_hit_rate": {"off": off["cache_hit_rate"]["mean"],
                               "on": on["cache_hit_rate"]["mean"]},
            "label": on["label"],
        },
        "gate": {"passed": not failed, "failed": failed,
                 "pass_drop_pts": drop, "p99_change_pct": p99_change,
                 "quality_drop_limit_pts": quality_drop_pts, "p99_limit_pct": p99_limit_pct},
        "kept": not failed,
    }


def run_ab(trial, reps: int = 3, *, label: str = "MEASURED") -> dict:
    """Run both arms for ``reps``. ``trial(on: bool, rep: int) -> per-rep dict``. Interleaved."""
    off_rows, on_rows = [], []
    for rep in range(1, reps + 1):
        for on in (False, True) if rep % 2 else (True, False):  # rotate order each rep
            (on_rows if on else off_rows).append(trial(on, rep))
    off = arm_summary("off", off_rows, label)
    on = arm_summary("on", on_rows, label)
    return evaluate_ab(off, on)


def mock_ab(reps: int = 3) -> dict:
    """Deterministic off/on arms for a CPU rehearsal. Every number MOCK.

    'on' keeps the shared prefix cached: lower prefill energy and TTFT, same answers, high hit rate.
    """
    def rows(on: bool):
        out = []
        for rep in range(reps):
            jitter = 1 + (rep - 1) * 0.004
            out.append({
                "j_per_req": round((7.1 if on else 11.6) * jitter, 4),
                "pass_pct": 96.0,
                "ttft_ms": round((120.0 if on else 430.0) * jitter, 2),
                "p99_ms": round((3200.0 if on else 3350.0) * jitter, 2),
                "cache_hit_rate": (0.82 if on else 0.0),
            })
        return out
    off = arm_summary("off", rows(False), "MOCK")
    on = arm_summary("on", rows(True), "MOCK")
    return evaluate_ab(off, on)
