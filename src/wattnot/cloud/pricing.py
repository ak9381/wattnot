"""Token prices and the USD arithmetic.

Rates are per million tokens. The default is the published Baseten rate for
Muse Spark Open (model library page, read 2026-10-27). They are not confirmed
against an invoice yet; `wattnot cloud audit` compares token totals with
Baseten's usage data, and `confirmed` stays false until someone checks the bill.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Rates:
    input_per_m: float = 1.25
    cached_per_m: float = 0.15
    output_per_m: float = 4.25
    source: str = "baseten.co/library/muse-spark-open (Model API pricing), read 2026-10-27"
    confirmed: bool = False  # true only after the rates were checked against an invoice

    def to_dict(self) -> dict:
        return {**asdict(self), "currency": "USD", "unit": "per 1M tokens"}


def cost_usd(rates: Rates, prompt_tokens: int, cached_tokens: int, completion_tokens: int) -> float:
    """Uncached input + cached input + output. Reasoning tokens are inside completion_tokens."""

    cached = max(0, min(cached_tokens, prompt_tokens))
    uncached = prompt_tokens - cached
    return (uncached * rates.input_per_m + cached * rates.cached_per_m
            + completion_tokens * rates.output_per_m) / 1_000_000
