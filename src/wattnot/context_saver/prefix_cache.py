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

import math
import re
import statistics as st
from collections.abc import Sequence

from wattnot.gates.noise import t_sf
from wattnot.proof.stats import ci95, mean_sd

SPEC = "wattnot.prefix-cache-ab/1"
P99_LIMIT_PCT = 10.0
QUALITY_DROP_PTS = 1.0

_QUERIES = re.compile(r"^vllm:prefix_cache_queries_total(?:\{[^}]*\})?\s+([0-9.eE+]+)", re.M)
_HITS = re.compile(r"^vllm:prefix_cache_hits_total(?:\{[^}]*\})?\s+([0-9.eE+]+)", re.M)
_GAUGE = re.compile(r"^vllm:gpu_prefix_cache_hit_rate(?:\{[^}]*\})?\s+([0-9.eE+]+)", re.M)


_TTFT_SUM = re.compile(r'^vllm:time_to_first_token_seconds_sum(?:\{[^}]*\})?\s+([0-9.eE+-]+)', re.M)
_TTFT_COUNT = re.compile(r'^vllm:time_to_first_token_seconds_count(?:\{[^}]*\})?\s+([0-9.eE+-]+)',
                         re.M)


def ttft_ms_from_metrics(before_text: str, after_text: str) -> float | None:
    """Mean time to first token (ms) over a window, from vLLM's TTFT histogram sum/count deltas.
    None if the histogram is absent or nothing was served in the window (never a made-up 0)."""
    def _sum(pat, text):
        vals = [float(x) for x in pat.findall(text)]
        return sum(vals) if vals else None
    s0, s1 = _sum(_TTFT_SUM, before_text), _sum(_TTFT_SUM, after_text)
    c0, c1 = _sum(_TTFT_COUNT, before_text), _sum(_TTFT_COUNT, after_text)
    if None in (s0, s1, c0, c1) or c1 - c0 <= 0 or s1 - s0 <= 0:
        return None
    return round((s1 - s0) / (c1 - c0) * 1000.0, 2)


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
    """Aggregate per-rep rows (j_per_req, pass_pct, ttft_ms, p99_ms, cache_hit_rate, and the
    reasoning-token share) for one arm."""
    out = {"arm": name, "reps": len(rows), "label": label}
    for key in ("j_per_req", "pass_pct", "ttft_ms", "p99_ms", "cache_hit_rate",
                "reasoning_token_share"):
        out[key] = _agg(rows, key)
    jpr = out["j_per_req"]["mean"]
    passm = out["pass_pct"]["mean"]
    out["j_per_correct"] = round(jpr / (passm / 100), 4) if jpr and passm else None
    # Per-rep values kept in rep order so evaluate_ab can pair off[i] with on[i] (run_ab appends
    # one off and one on per rep, in rep order) and compute a CI + p-value on the PAIRED
    # differences, the same rigour the power-cap sweep uses (wattnot.proof.session.summarize).
    def _jpc(r: dict) -> float | None:
        j, p = r.get("j_per_req"), r.get("pass_pct")
        return round(j / (p / 100), 4) if j and p else None
    out["j_per_req_reps"] = [r.get("j_per_req") for r in rows]
    out["j_per_correct_reps"] = [_jpc(r) for r in rows]
    # request errors across this arm's reps: an errored request grades as a wrong answer and its
    # energy is still counted, so it must be surfaced, not silently absorbed into the pass rate.
    out["errors"] = sum((r.get("errors") or 0) for r in rows)
    # label the reasoning share: MOCK for a rehearsal, else MEASURED only when every rep's count
    # came from server usage, otherwise ESTIMATE (sized from the reasoning text)
    measured = all(r.get("reasoning_measured") for r in rows) if rows else False
    out["reasoning_label"] = label if label == "MOCK" else ("MEASURED" if measured else "ESTIMATE")
    return out


def _change_pct(a, b):
    return round(100 * (b - a) / a, 2) if a and b is not None else None


def paired_change(off_vals: Sequence[float | None], on_vals: Sequence[float | None]) -> dict:
    """CI and p-value on the PAIRED per-rep change (on vs off), the same rigour the power-cap
    sweep uses (wattnot.proof.session.summarize): pair rep i of each arm, take the per-rep percent
    change, and report its mean, the half-width of its 95% CI (Student t, reps-1 df), whether that
    CI is entirely below zero (a real saving, not noise), and a two-sided paired-t p-value.

    off_vals[i] and on_vals[i] must be the same rep. Reps where either arm is missing a value are
    dropped from the pair set. Returns all-None fields when fewer than two usable pairs survive
    (n<2 can carry no CI), so a thin run reports "not enough reps", never a fake certainty.
    """
    pairs = [(o, n) for o, n in zip(off_vals, on_vals)
             if o not in (None, 0) and n is not None]
    deltas = [100.0 * (n - o) / o for o, n in pairs]
    n = len(deltas)
    if n < 2:
        return {"mean": None, "ci95_half": None, "ci95_excludes_zero": None,
                "below_zero": None, "p_value": None, "n": n, "per_pair": deltas}
    m, half = ci95(deltas)
    _, sd = mean_sd(deltas)
    if sd == 0:   # every rep moved by the identical fraction: a definite, zero-variance result
        p_value = 0.0 if m != 0 else 1.0
    else:
        t = m / (sd / math.sqrt(n))
        p_value = round(min(1.0, 2 * t_sf(abs(t), n - 1)), 6)  # two-sided
    excludes_zero = (m + half < 0) or (m - half > 0)
    return {"mean": round(m, 4), "ci95_half": round(half, 4),
            "ci95_excludes_zero": bool(excludes_zero), "below_zero": bool(m < 0),
            "p_value": p_value, "n": n, "per_pair": [round(d, 4) for d in deltas]}


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
    # Any request error in either arm blocks the gate, the same rule the power-cap sweep uses
    # (wattnot.proof.session.summarize): an errored request grades as wrong and its energy is
    # counted, so an error on the stock/off arm would flatter the on arm's J/correct saving.
    n_err = (off.get("errors") or 0) + (on.get("errors") or 0)
    if n_err:
        failed.append(f"{n_err} request errors across the arms (off {off.get('errors') or 0}, "
                      f"on {on.get('errors') or 0}): errors can flatter the J/correct saving")
    # CI + p-value on the PAIRED per-rep difference (item 7): the headline saving % is no longer a
    # bare mean-to-mean point estimate. j_per_req is the clean energy signal (no pass-rate mixed
    # in); j_per_correct is reported too for the headline number the combined block quotes.
    energy_paired = paired_change(off.get("j_per_req_reps") or [], on.get("j_per_req_reps") or [])
    jpc_paired = paired_change(off.get("j_per_correct_reps") or [], on.get("j_per_correct_reps") or [])
    # A saving is "significant" only when the paired CI is entirely below zero AND p < 0.05 — the
    # same bar the sweep's energy gate uses. Reported, not a hard gate here: the combined block
    # can run as few as 2 reps, where a true saving may still miss significance; the flag keeps the
    # headline honest instead of asserting a saving the n cannot support.
    energy_saving_significant = bool(
        energy_paired["below_zero"] and energy_paired["ci95_excludes_zero"]
        and energy_paired["p_value"] is not None and energy_paired["p_value"] < 0.05)
    return {
        "spec": SPEC,
        "lever": "enable_prefix_caching",
        "arms": {"off": off, "on": on},
        "headline": {
            "j_per_correct_change_pct": _change_pct(off["j_per_correct"], on["j_per_correct"]),
            "ttft_change_pct": _change_pct(off["ttft_ms"]["mean"], on["ttft_ms"]["mean"]),
            "p99_change_pct": p99_change,
            # paired (per-rep) uncertainty on the energy saving; None fields when < 2 reps
            "j_per_req_change_pct_paired": energy_paired,
            "j_per_correct_change_pct_paired": jpc_paired,
            "energy_saving_significant": energy_saving_significant,
            "cache_hit_rate": {"off": off["cache_hit_rate"]["mean"],
                               "on": on["cache_hit_rate"]["mean"]},
            "reasoning_token_share": {"off": (off.get("reasoning_token_share") or {}).get("mean"),
                                      "on": (on.get("reasoning_token_share") or {}).get("mean"),
                                      "label": on.get("reasoning_label")},
            "label": on["label"],
        },
        "gate": {"passed": not failed, "failed": failed,
                 "pass_drop_pts": drop, "p99_change_pct": p99_change,
                 "errors": {"off": off.get("errors") or 0, "on": on.get("errors") or 0},
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
                # prefix caching skips prefill, not thinking: the reasoning share barely moves
                "reasoning_token_share": 0.58,
                "reasoning_measured": False,
            })
        return out
    off = arm_summary("off", rows(False), "MOCK")
    on = arm_summary("on", rows(True), "MOCK")
    return evaluate_ab(off, on)
