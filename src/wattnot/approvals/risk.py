"""Risk levels for the approve-before-apply guardrail.

A small cap tweak near stock is low risk and can go through on its own once the
gates pass. Anything else waits for a person. When in doubt the answer is HIGH:
a change is low only when every number needed to call it low is present.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from wattnot.approvals import model as m

FORMAT = "wattnot.guardrail/1"
FILENAME = "guardrail.json"
_KNOWN_KEYS = ("low_cap_drop_pct", "high_below_tdp_pct", "max_gpus_auto",
               "approval_timeout_s")


@dataclass(frozen=True)
class RiskConfig:
    low_cap_drop_pct: float = 15
    high_below_tdp_pct: float = 70
    max_gpus_auto: int = 1
    approval_timeout_s: float = 600

    @staticmethod
    def from_dict(d: Mapping) -> RiskConfig:
        if not isinstance(d, Mapping):
            raise ValueError("guardrail config must be an object")
        if d.get("format", FORMAT) != FORMAT:
            raise ValueError(f"unknown guardrail format {d.get('format')!r}")
        unknown = sorted(set(d) - {"format", *_KNOWN_KEYS})
        if unknown:
            raise ValueError(f"unknown guardrail key: {', '.join(unknown)}")
        kw: dict = {}
        for key in ("low_cap_drop_pct", "high_below_tdp_pct"):
            v = d.get(key, 15 if key == "low_cap_drop_pct" else 70)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 100:
                raise ValueError(f"{key} must be a percent between 0 and 100")
            kw[key] = v
        gpus = d.get("max_gpus_auto", 1)
        if isinstance(gpus, bool) or not isinstance(gpus, int) or gpus < 1:
            raise ValueError("max_gpus_auto must be a whole number >= 1")
        kw["max_gpus_auto"] = gpus
        timeout = d.get("approval_timeout_s", 600)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("approval_timeout_s must be over 0")
        kw["approval_timeout_s"] = timeout
        return RiskConfig(**kw)

    def to_dict(self) -> dict:
        return {"format": FORMAT, "low_cap_drop_pct": self.low_cap_drop_pct,
                "high_below_tdp_pct": self.high_below_tdp_pct,
                "max_gpus_auto": self.max_gpus_auto,
                "approval_timeout_s": self.approval_timeout_s}


def load_config(root: str | Path) -> RiskConfig:
    """Read `<root>/guardrail.json`. A missing file means the defaults."""
    f = Path(root) / FILENAME
    if not f.is_file():
        return RiskConfig()
    try:
        doc = json.loads(f.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ValueError(f"{FILENAME} is not valid JSON: {e}") from e
    return RiskConfig.from_dict(doc)


@dataclass(frozen=True)
class Risk:
    level: str  # "low" or "high"
    needs_approval: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)
    inputs: dict = field(default_factory=dict)


def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _gpu_count(change: Mapping) -> int:
    gpus = change.get("gpus")
    if isinstance(gpus, list):
        return len(gpus)
    for key in ("num_gpus", "gpu_count"):
        n = change.get(key)
        if isinstance(n, int) and not isinstance(n, bool):
            return n
    after = change.get("after")
    if isinstance(after, Mapping):
        for key in ("num_gpus", "gpu_count"):
            n = after.get(key)
            if isinstance(n, int) and not isinstance(n, bool):
                return n
    return 1


def _risk(level: str, reasons: list[str], inputs: dict) -> Risk:
    return Risk(level=level, needs_approval=level == "high",
                reasons=tuple(reasons), inputs=inputs)


def classify(proposal_or_spec: Mapping, cfg: RiskConfig | None = None, *,
             stock_cap_w: float | None = None, tdp_w: float | None = None) -> Risk:
    """Rate a proposal or spec. Anything not clearly low is HIGH."""
    cfg = cfg or RiskConfig()
    kind = proposal_or_spec.get("kind")
    change = proposal_or_spec.get("change")
    if not isinstance(change, Mapping):
        return _risk("high", ["no change block to review"], {})
    if kind == m.POWER_CAP:
        return _power_cap(change, cfg, stock_cap_w, tdp_w)
    if kind == m.VLLM_CONFIG:
        return _vllm_config(change, cfg)
    if kind == m.MOE_ROUTING:
        return _risk("high", ["adaptive routing changes what the model says; a person decides"],
                     {"kind": kind})
    if kind == m.FLEX_SHED:
        return _risk("high", ["a shed cuts power for a live event; a person decides"],
                     {"kind": kind})
    if kind == m.TRIAL_GRANT:
        return _risk("high", ["permission to touch hardware; a person approves it"],
                     {"kind": kind})
    if kind == m.CLOUD_POLICY:
        before = change.get("before")
        if not isinstance(before, Mapping) or not before:
            return _risk("high", ["no current policy to compare with"], {"kind": kind})
        return _risk("low", ["no hardware touched"], {"kind": kind})
    return _risk("high", [f"unknown kind {kind!r}; a person decides"], {"kind": kind})


def _power_cap(change: Mapping, cfg: RiskConfig, stock_cap_w, tdp_w) -> Risk:
    before = change.get("before") or {}
    after = change.get("after") or {}
    new = _num(after.get("power_cap_w")) if isinstance(after, Mapping) else None
    stock = _num(before.get("power_cap_w")) if isinstance(before, Mapping) else None
    if stock is None:
        stock = _num(stock_cap_w)
    tdp = _num(tdp_w)
    if tdp is None:
        tdp = stock
    n_gpus = _gpu_count(change)
    inputs = {"new_cap_w": new, "stock_cap_w": stock, "tdp_w": tdp, "gpus": n_gpus}
    if new is None:
        return _risk("high", ["no new power cap to review"], inputs)
    if stock is None:
        return _risk("high", ["no stock cap to compare with"], inputs)
    if tdp is None:
        return _risk("high", ["no card TDP to compare with"], inputs)
    if new >= stock:
        return _risk("low", ["raises the cap, or leaves it where it is"], inputs)
    reasons: list[str] = []
    drop_pct = (stock - new) / stock * 100
    inputs["drop_pct"] = round(drop_pct, 2)
    inputs["tdp_pct"] = round(new / tdp * 100, 2) if tdp > 0 else None
    if drop_pct > cfg.low_cap_drop_pct:
        reasons.append(f"more than {cfg.low_cap_drop_pct:g}% below stock")
    if tdp > 0 and new < cfg.high_below_tdp_pct / 100 * tdp:
        reasons.append(f"below {cfg.high_below_tdp_pct:g}% of the card's TDP")
    if n_gpus > cfg.max_gpus_auto:
        reasons.append(f"touches {n_gpus} GPUs (limit {cfg.max_gpus_auto})")
    if reasons:
        return _risk("high", reasons, inputs)
    return _risk("low", [f"within {cfg.low_cap_drop_pct:g}% of stock"], inputs)


def _vllm_config(change: Mapping, cfg: RiskConfig) -> Risk:
    before = change.get("before")
    after = change.get("after")
    if not isinstance(after, Mapping):
        return _risk("high", ["no new config to review"], {"kind": m.VLLM_CONFIG})
    bq = before.get("quantization") if isinstance(before, Mapping) else None
    aq = after.get("quantization")
    n_gpus = _gpu_count(change)
    inputs = {"quantization_before": bq, "quantization_after": aq, "gpus": n_gpus}
    if aq != bq:
        return _risk("high", ["changes quantization; a person decides"], inputs)
    if n_gpus > cfg.max_gpus_auto:
        return _risk("high", [f"touches {n_gpus} GPUs (limit {cfg.max_gpus_auto})"], inputs)
    return _risk("low", ["batch size, KV cache and other tweaks only"], inputs)
