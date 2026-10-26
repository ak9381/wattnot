"""Quality and energy gate for Spark Open tries.

Quality: each try's answers are compared with a reference config. The reference
is either a hosted full-precision endpoint (Baseten or Modal, optional config,
off by default) or the highest local quant at the largest VRAM budget.
A try passes when agreement drops at most 1 point and p99 stays within 10%.

Energy: a saving counts only when GPU + host energy per output token goes down.
If host power is unknown the saving is shown as ESTIMATE and never counted, so
a "saving" that only moved work from the GPU to the CPU can't slip through.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from wattnot.spark_open.hostpower import GPU_ONLY

QUALITY_DROP_PTS = 1.0
P99_SLACK = 0.10
SHIFT_TOLERANCE = 0.25  # host rise >= 75% of the GPU drop counts as a shift


@dataclass(frozen=True)
class HostedReference:
    """Optional hosted reference. Nothing calls it unless `enabled` is True."""

    provider: str = ""          # "baseten" or "modal"
    base_url: str = ""
    model: str = "meta-models/Muse-Spark-Open-816B-A42B"
    api_key_env: str = ""       # name of the env var, never the key itself
    enabled: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> HostedReference:
        url = env.get("WATTNOT_SPARK_REF_URL", "")
        return cls(provider=env.get("WATTNOT_SPARK_REF_PROVIDER", ""), base_url=url,
                   api_key_env=env.get("WATTNOT_SPARK_REF_KEY_ENV", ""),
                   enabled=bool(url) and env.get("WATTNOT_SPARK_REF_ENABLE") == "1")

    def describe(self) -> dict:
        return {"provider": self.provider or None, "base_url": self.base_url or None,
                "model": self.model, "enabled": self.enabled}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def agreement_pct(ref: Mapping[str, str], cand: Mapping[str, str]) -> float:
    """Share of items whose normalized answer matches the reference, in percent."""

    ids = [i for i in ref if i in cand]
    if not ids:
        return 0.0
    return 100.0 * sum(_norm(ref[i]) == _norm(cand[i]) for i in ids) / len(ids)


def p99(xs: Sequence[float]) -> float:
    s = sorted(xs)
    if not s:
        return 0.0
    k = max(0, min(len(s) - 1, int(round(0.99 * (len(s) - 1)))))
    return s[k]


@dataclass(frozen=True)
class Energy:
    gpu_j: float
    host_j: float | None
    out_tokens: int

    @property
    def total_j(self) -> float | None:
        return None if self.host_j is None else self.gpu_j + self.host_j

    def per_token(self, total: bool) -> float | None:
        j = self.total_j if total else self.gpu_j
        return None if j is None or self.out_tokens <= 0 else j / self.out_tokens


def energy_verdict(base: Energy, cand: Energy, *, simulated: bool = False) -> dict:
    """Compare per-token energy. Counted only on the GPU+host boundary."""

    host_ok = base.host_j is not None and cand.host_j is not None
    gb, gc = base.per_token(False), cand.per_token(False)
    gpu_saving = None if not gb or gc is None else round(100 * (gb - gc) / gb, 3)
    out = {"boundary": "GPU+host" if host_ok else GPU_ONLY, "gpu_saving_pct": gpu_saving,
           "total_saving_pct": None, "counted": False, "shifted_to_cpu": False, "flag": None,
           "objective": "whole-node J per output token (GPU + CPU package + DRAM)"}
    measured = "SIMULATED" if simulated else "MEASURED"
    if not host_ok:
        out.update(label="SIMULATED" if simulated else "ESTIMATE",
                   reason="host power unavailable; GPU-only saving is not counted")
        return out
    tb, tc = base.per_token(True), cand.per_token(True)
    total = None if not tb or tc is None else round(100 * (tb - tc) / tb, 3)
    # Load shift: GPU energy fell, host energy rose by a similar amount (within
    # SHIFT_TOLERANCE of the GPU drop), so whole-node energy barely moved.
    gpu_drop = base.gpu_j / base.out_tokens - cand.gpu_j / cand.out_tokens
    host_rise = base.host_j / base.out_tokens if base.out_tokens else 0.0
    host_rise = cand.host_j / cand.out_tokens - host_rise
    shifted = bool(gpu_drop > 0 and host_rise > 0
                   and (host_rise >= gpu_drop * (1 - SHIFT_TOLERANCE)
                        or total is None or total <= 0))
    out.update(total_saving_pct=total, label=measured, shifted_to_cpu=shifted,
               counted=bool(total is not None and total > 0 and not shifted),
               flag="shifted, not saved" if shifted else None,
               reason=("GPU energy fell but CPU+DRAM rose by about as much"
                       if shifted else "GPU+host per-token energy compared"))
    return out


def quality_verdict(ref_out: Mapping[str, str], ref_lat: Sequence[float],
                    cand_out: Mapping[str, str], cand_lat: Sequence[float], *,
                    ref_agreement: float = 100.0) -> dict:
    agree = agreement_pct(ref_out, cand_out)
    drop = round(ref_agreement - agree, 3)
    rp, cp = p99(ref_lat), p99(cand_lat)
    p99_ok = rp <= 0 or cp <= rp * (1 + P99_SLACK)
    return {"agreement_pct": round(agree, 3), "drop_pts": drop,
            "quality_ok": drop <= QUALITY_DROP_PTS, "p99_s": round(cp, 4),
            "ref_p99_s": round(rp, 4), "p99_ok": p99_ok,
            "passed": drop <= QUALITY_DROP_PTS and p99_ok}
