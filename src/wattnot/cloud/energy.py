"""Energy ESTIMATE for hosted calls. Always a range, always labelled ESTIMATE.

We cannot see Baseten's GPUs, so nothing here is measured. The estimate counts
FLOPs from the token counts we did measure and converts them with a stated
joules-per-FLOP range for H100-class hardware.

* A forward pass costs about 2 x active parameters FLOPs per token.
  Muse Spark Open is a mixture of experts with 42B active of 816B total.
* Cached prompt tokens skip prefill, so they cost nothing here.
* Decode is memory bound, so real efficiency is far below peak. The range spans
  about 40% to 4% utilisation of an H100 (989 TFLOPs dense BF16 at 700 W):
  1.8e-12 to 1.8e-11 J/FLOP.

This says how much work was avoided, not how many kWh Baseten saved. It never
becomes a carbon claim.
"""

from __future__ import annotations

ACTIVE_PARAMS = 42e9
J_PER_FLOP = (1.8e-12, 1.8e-11)
METHOD = ("2 x active params x (uncached prompt + completion tokens) FLOPs, times a J/FLOP range "
          "for H100-class GPUs. Cached prompt tokens skip prefill. Not measured on Baseten's GPUs.")


def flops(prompt_tokens: int, cached_tokens: int, completion_tokens: int,
          active_params: float = ACTIVE_PARAMS) -> float:
    cached = max(0, min(cached_tokens, prompt_tokens))
    return 2 * active_params * ((prompt_tokens - cached) + completion_tokens)


def wh_range(prompt_tokens: int, cached_tokens: int, completion_tokens: int, *,
             active_params: float = ACTIVE_PARAMS,
             j_per_flop: tuple[float, float] = J_PER_FLOP) -> tuple[float, float]:
    f = flops(prompt_tokens, cached_tokens, completion_tokens, active_params)
    return f * j_per_flop[0] / 3600, f * j_per_flop[1] / 3600


def estimate(tuned: dict, baseline: dict, *, active_params: float = ACTIVE_PARAMS,
             j_per_flop: tuple[float, float] = J_PER_FLOP) -> dict:
    """tuned/baseline: dicts with prompt, cached, completion token totals."""

    t = wh_range(tuned["prompt"], tuned["cached"], tuned["completion"],
                 active_params=active_params, j_per_flop=j_per_flop)
    b = wh_range(baseline["prompt"], baseline["cached"], baseline["completion"],
                 active_params=active_params, j_per_flop=j_per_flop)
    return {"label": "ESTIMATE", "unit": "Wh", "method": METHOD,
            "assumptions": {"active_params": active_params, "j_per_flop_low": j_per_flop[0],
                            "j_per_flop_high": j_per_flop[1]},
            "tuned_wh": {"low": round(t[0], 6), "high": round(t[1], 6)},
            "baseline_wh": {"low": round(b[0], 6), "high": round(b[1], 6)},
            "saved_wh": {"low": round(b[0] - t[0], 6), "high": round(b[1] - t[1], 6)}}
