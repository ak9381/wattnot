"""Scale a measured per-request saving to a fleet. Every number is an ESTIMATE.

Only one input is measured: the Wh-per-request delta from a WattNot receipt.
The rest are assumptions you pass in (or the defaults below):

- tariff: 0.12 USD per kWh. An assumption (roughly a US commercial rate);
  pass your own with --tariff-usd-per-kwh.
- WUE: 1.8 L of water per kWh of IT energy, a common data-centre average.
- PUE: 1.0, so only GPU-side energy is counted unless you say otherwise.
- month: 30 days.

    monthly_kwh = wh_delta * requests_per_gpu_per_day * gpus * days * pue / 1000
    dollars     = monthly_kwh * tariff
    litres      = monthly_kwh * wue
"""

from __future__ import annotations

from dataclasses import dataclass

TARIFF_USD_PER_KWH = 0.12  # assumption, not measured
WUE_L_PER_KWH = 1.8
PUE = 1.0
DAYS = 30


@dataclass(frozen=True)
class Impact:
    kwh: float
    usd: float
    litres: float


def estimate(wh_delta: float, requests_per_gpu_per_day: float, gpus: int, *,
             tariff_usd_per_kwh: float = TARIFF_USD_PER_KWH, wue_l_per_kwh: float = WUE_L_PER_KWH,
             pue: float = PUE, days: float = DAYS) -> Impact:
    if requests_per_gpu_per_day < 0 or gpus < 1 or days <= 0:
        raise ValueError("requests, gpus and days must be positive")
    if pue < 1 or tariff_usd_per_kwh < 0 or wue_l_per_kwh < 0:
        raise ValueError("pue must be >= 1; tariff and wue must be >= 0")
    kwh = wh_delta * requests_per_gpu_per_day * gpus * days * pue / 1000
    return Impact(kwh, kwh * tariff_usd_per_kwh, kwh * wue_l_per_kwh)


def report(i: Impact, *, gpus: int, days: float) -> list[str]:
    word = "saved" if i.kwh >= 0 else "added"
    return [
        f"ESTIMATE energy {word}: {abs(i.kwh):,.1f} kWh per {days:g} days ({gpus} GPUs)",
        f"ESTIMATE cost {word}: ${abs(i.usd):,.2f}",
        f"ESTIMATE water {word}: {abs(i.litres):,.0f} L",
        "ESTIMATE note: scaled from one measured Wh/request delta; tariff, WUE, PUE "
        "and traffic are assumptions",
    ]
