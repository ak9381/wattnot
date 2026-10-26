"""GPU energy meter: 10 Hz traces, a SHA-256 next to each CSV, idle windows."""

from __future__ import annotations

from typing import Any

from wattnot.meter.attribution import attribute, split_wh
from wattnot.meter.base import Meter, Window, trace_watt_hours
from wattnot.meter.dcgm import DcgmMeter
from wattnot.meter.nvml import NvmlMeter
from wattnot.meter.zeus import ZeusMeter

BACKENDS = {"nvml": NvmlMeter, "dcgm": DcgmMeter, "zeus": ZeusMeter}


def open_meter(name: str, run_id: str, **kwargs: Any) -> Meter:
    """Open a live meter for `--meter nvml|dcgm|zeus`."""

    try:
        cls = BACKENDS[name]
    except KeyError:
        raise ValueError(f"unknown meter {name!r}; pick one of {sorted(BACKENDS)}") from None
    return cls.open(run_id, **kwargs)


__all__ = [
    "BACKENDS",
    "DcgmMeter",
    "Meter",
    "NvmlMeter",
    "Window",
    "ZeusMeter",
    "attribute",
    "open_meter",
    "split_wh",
    "trace_watt_hours",
]
