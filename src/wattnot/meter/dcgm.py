"""DCGM meter (stub backend). Same 10 Hz sampler and CSV as NVML.

DCGM is the datacenter path: a host running `nv-hostengine` exposes the
same counters fleet-wide. The reader is injected so tests run without it.
A reader needs `gpu_ids() -> list[int]` and `read(gpu) -> dict` with the
DCGM field names below. `DcgmMeter.open` raises until the live binding is
wired (it is not needed for the demo, which uses NVML on one box).
"""

from __future__ import annotations

from typing import Any

from wattnot.meter.base import GpuSample, SamplingMeter

# DCGM field ids by name (dcgm_fields.h).
FIELDS = {
    "DCGM_FI_DEV_POWER_USAGE": 155,  # W
    "DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION": 156,  # mJ since driver load
    "DCGM_FI_DEV_SM_CLOCK": 100,
    "DCGM_FI_DEV_MEM_CLOCK": 101,
    "DCGM_FI_DEV_GPU_TEMP": 150,
    "DCGM_FI_DEV_GPU_UTIL": 203,
}


class DcgmUnavailable(RuntimeError):
    """The DCGM host engine or its Python binding is not present."""


class DcgmMeter(SamplingMeter):
    source = "dcgm"

    def __init__(self, reader: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._reader = reader
        self._gpus: list[int] = []

    @classmethod
    def open(cls, run_id: str, **kwargs: Any) -> DcgmMeter:
        raise DcgmUnavailable("live DCGM binding is not wired yet; use --meter nvml")

    def _prepare(self) -> None:
        gpus = [int(g) for g in self._reader.gpu_ids()]
        if not gpus:
            raise DcgmUnavailable("DCGM reports no GPUs")
        self._gpus = gpus

    def _sample_gpus(self) -> list[GpuSample]:
        out = []
        for gpu in self._gpus:
            row = self._reader.read(gpu)
            energy = row.get("DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION")
            util = row.get("DCGM_FI_DEV_GPU_UTIL")
            out.append(
                GpuSample(
                    gpu=gpu,
                    power_w=float(row["DCGM_FI_DEV_POWER_USAGE"]),
                    energy_mj=None if energy is None else int(energy),
                    sm_mhz=int(row.get("DCGM_FI_DEV_SM_CLOCK", 0)),
                    mem_mhz=int(row.get("DCGM_FI_DEV_MEM_CLOCK", 0)),
                    temp_c=int(row.get("DCGM_FI_DEV_GPU_TEMP", 0)),
                    util_pct=None if util is None else int(util),
                )
            )
        return out
