"""NVML meter. Reads the same device object the probe uses.

Pass the in-memory fake from tests, or a live `NvmlLib` from `open_nvml`.
This module does not open the driver itself except through `NvmlMeter.open`,
which refuses to run while the test suite is up.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from pathlib import Path

from wattnot.meter.base import SAMPLE_HZ, GpuSample, SamplingMeter
from wattnot.probe import CLOCK_MEM, CLOCK_SM, NoGpu, NvmlError, open_nvml


class _Utilization(ctypes.Structure):
    """nvmlUtilization_t: GPU and memory busy, each a percent."""

    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class NvmlMeter(SamplingMeter):
    """Sample every visible GPU through NVML at 10 Hz."""

    source = "nvml"

    def __init__(
        self,
        nvml: object,
        *,
        run_id: str,
        traces_root: str | Path = "traces",
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        hz: float = SAMPLE_HZ,
        on_sample: Callable[[dict], None] | None = None,
    ) -> None:
        super().__init__(
            run_id=run_id,
            traces_root=traces_root,
            clock=clock,
            sleep=sleep,
            hz=hz,
            on_sample=on_sample,
        )
        self._nvml = nvml
        self._handles: list[tuple[int, object]] = []

    @classmethod
    def open(cls, run_id: str, **kwargs: object) -> NvmlMeter:
        """Open the local driver. Raises NvmlUnavailable under pytest."""

        return cls(open_nvml(), run_id=run_id, **kwargs)

    def _prepare(self) -> None:
        nvml = self._nvml
        try:
            count = int(nvml.device_count())
        except NvmlError as exc:
            raise NoGpu(str(exc)) from exc
        if count < 1:
            raise NoGpu("no GPU")
        handles: list[tuple[int, object]] = []
        for index in range(count):
            try:
                handle = nvml.device_handle(index)
            except NvmlError as exc:
                raise NoGpu(str(exc)) from exc
            handles.append((index, handle))
        self._handles = handles

    def _sample_gpus(self) -> list[GpuSample]:
        return [self._one(index, handle) for index, handle in self._handles]

    def _one(self, index: int, handle: object) -> GpuSample:
        nvml = self._nvml
        power_mw = int(nvml.power_usage_mw(handle))
        return GpuSample(
            gpu=index,
            power_w=power_mw / 1000.0,
            energy_mj=_energy_mj(nvml, handle),
            sm_mhz=int(nvml.clock_mhz(handle, CLOCK_SM)),
            mem_mhz=int(nvml.clock_mhz(handle, CLOCK_MEM)),
            temp_c=int(nvml.temperature_c(handle)),
            util_pct=_util_pct(nvml, handle),
        )


def _energy_mj(nvml: object, handle: object) -> int | None:
    try:
        return int(nvml.total_energy_mj(handle))
    except NvmlError:
        return None


def _util_pct(nvml: object, handle: object) -> int | None:
    """Percent busy. A backend may offer `utilization_pct`; the driver library is the other path.

    Missing or unsupported util is left blank. It must not fail the energy window.
    """

    reader = getattr(nvml, "utilization_pct", None)
    if reader is not None:
        try:
            value = reader(handle)
        except NvmlError:
            return None
        if value is None:
            return None
        return int(value)
    return _util_from_driver(nvml, handle)


def _util_from_driver(nvml: object, handle: object) -> int | None:
    """Call nvmlDeviceGetUtilizationRates when this object wraps the real library.

    Only a ctypes function pointer is called. Test fakes do not have one, so
    this stays off the test path and a failure becomes a blank cell.
    """

    lib = getattr(nvml, "_lib", None)
    fn = getattr(lib, "nvmlDeviceGetUtilizationRates", None) if lib is not None else None
    if fn is None or type(fn).__name__ != "_FuncPtr":
        return None
    try:
        fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Utilization)]
        fn.restype = ctypes.c_int
        util = _Utilization()
        code = int(fn(handle, ctypes.byref(util)))
    except (OSError, TypeError, ValueError):
        return None
    if code != 0:
        return None
    return int(util.gpu)
