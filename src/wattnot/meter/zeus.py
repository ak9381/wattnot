"""Zeus meter (stub backend). Same 10 Hz sampler and CSV as NVML.

Zeus (ml.energy) reads GPU energy through its own monitor. The monitor is
injected so tests run without the package. It needs `gpu_indices` and
`power_w(gpu) -> float`, `energy_j(gpu) -> float | None`. Zeus reports
joules; the CSV stores millijoules like NVML. `ZeusMeter.open` raises until
the live binding is wired.
"""

from __future__ import annotations

from typing import Any

from wattnot.meter.base import GpuSample, SamplingMeter


class ZeusUnavailable(RuntimeError):
    """The zeus package is not installed or found no GPUs."""


class ZeusMeter(SamplingMeter):
    source = "zeus"

    def __init__(self, monitor: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._monitor = monitor
        self._gpus: list[int] = []

    @classmethod
    def open(cls, run_id: str, **kwargs: Any) -> ZeusMeter:
        raise ZeusUnavailable("live Zeus binding is not wired yet; use --meter nvml")

    def _prepare(self) -> None:
        gpus = [int(g) for g in self._monitor.gpu_indices]
        if not gpus:
            raise ZeusUnavailable("Zeus reports no GPUs")
        self._gpus = gpus

    def _sample_gpus(self) -> list[GpuSample]:
        out = []
        for gpu in self._gpus:
            joules = self._monitor.energy_j(gpu)
            out.append(
                GpuSample(
                    gpu=gpu,
                    power_w=float(self._monitor.power_w(gpu)),
                    energy_mj=None if joules is None else int(round(joules * 1000)),
                    sm_mhz=0,
                    mem_mhz=0,
                    temp_c=0,
                    util_pct=None,
                )
            )
        return out
