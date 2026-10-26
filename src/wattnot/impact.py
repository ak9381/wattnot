"""Scale a measured per-request saving to a fleet. Every number is an ESTIMATE.

Only one input is measured: the Wh-per-request delta from a WattNot receipt.
The rest are assumptions you pass in (or the defaults below):

- tariff: 8 INR per kWh, a typical Indian commercial/industrial rate.
- WUE: 1.8 L of water per kWh of IT energy, a common data-centre average.
- PUE: 1.0, so only GPU-side energy is counted unless you say otherwise.
- month: 30 days.

    monthly_kwh = wh_delta * requests_per_gpu_per_day * gpus * days * pue / 1000
    rupees      = monthly_kwh * tariff
    litres      = monthly_kwh * wue
"""

from __future__ import annotations

from dataclasses import dataclass

TARIFF_INR_PER_KWH = 8.0
WUE_L_PER_KWH = 1.8
PUE = 1.0
DAYS = 30


@dataclass(frozen=True)
class Impact:
    kwh: float
    inr: float
    litres: float


def estimate(wh_delta: float, requests_per_gpu_per_day: float, gpus: int, *,
             tariff_inr_per_kwh: float = TARIFF_INR_PER_KWH, wue_l_per_kwh: float = WUE_L_PER_KWH,
             pue: float = PUE, days: float = DAYS) -> Impact:
    if requests_per_gpu_per_day < 0 or gpus < 1 or days <= 0:
        raise ValueError("requests, gpus and days must be positive")
    if pue < 1 or tariff_inr_per_kwh < 0 or wue_l_per_kwh < 0:
        raise ValueError("pue must be >= 1; tariff and wue must be >= 0")
    kwh = wh_delta * requests_per_gpu_per_day * gpus * days * pue / 1000
    return Impact(kwh, kwh * tariff_inr_per_kwh, kwh * wue_l_per_kwh)


def report(i: Impact, *, gpus: int, days: float) -> list[str]:
    word = "saved" if i.kwh >= 0 else "added"
    return [
        f"ESTIMATE energy {word}: {abs(i.kwh):,.1f} kWh per {days:g} days ({gpus} GPUs)",
        f"ESTIMATE cost {word}: INR {abs(i.inr):,.0f}",
        f"ESTIMATE water {word}: {abs(i.litres):,.0f} L",
        "ESTIMATE note: scaled from one measured Wh/request delta; tariff, WUE, PUE "
        "and traffic are assumptions",
    ]
