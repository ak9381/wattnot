"""In-memory NVML stand-in for probe tests.

No library is loaded and no device file is opened. Power-cap and clock
changes stay in this object so a test can prove they were undone.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from wattnot.probe import NvmlError

PERMISSION_DENIED = "Insufficient Permissions"
# Default cap the fake boots with. Tests that kill the process compare against this.
DEFAULT_POWER_LIMIT_MW = 500_000


class FakeNVML:
    """One fake GPU with a power limit and an optional clock lock."""

    def __init__(
        self,
        *,
        name: str = "H100 80GB",
        driver: str = "550.90.07",
        cuda: int = 12040,
        device_count: int = 1,
        power_mw: int = 250_000,
        energy_mj: int = 1_000_000,
        energy_supported: bool = True,
        fail_core_reads: bool = False,
        sm_mhz: int = 1800,
        mem_mhz: int = 2619,
        temp_c: int = 42,
        min_limit_mw: int = 200_000,
        max_limit_mw: int = 700_000,
        default_limit_mw: int = 700_000,
        power_limit_mw: int = DEFAULT_POWER_LIMIT_MW,
        allow_cap_write: bool = True,
        allow_clock_lock: bool = False,
        journal_path: Path | None = None,
        raise_after_set: bool = False,
        raise_after_lock: bool = False,
    ) -> None:
        self.name = name
        self.driver = driver
        self.cuda = cuda
        self._device_count = device_count
        self.power_mw = power_mw
        self.energy_mj = energy_mj
        self.energy_supported = energy_supported
        self.fail_core_reads = fail_core_reads
        self.sm_mhz = sm_mhz
        self.mem_mhz = mem_mhz
        self.temp_c = temp_c
        self.min_limit_mw = min_limit_mw
        self.max_limit_mw = max_limit_mw
        self.default_limit_mw = default_limit_mw
        self.current_limit_mw = power_limit_mw
        self.allow_cap_write = allow_cap_write
        self.allow_clock_lock = allow_clock_lock
        self.journal_path = journal_path
        self._raise_after_set = raise_after_set
        self._raise_after_lock = raise_after_lock
        self.power_history: list[int] = []
        self.locked: tuple[int, int] | None = None
        self.lock_calls: list[tuple[int, int]] = []
        self.reset_calls = 0
        self.shutdown_called = False
        self.after_set: Callable[[int], None] | None = None

    def shutdown(self) -> None:
        self.shutdown_called = True

    def device_count(self) -> int:
        return self._device_count

    def device_handle(self, index: int) -> int:
        if index < 0 or index >= self._device_count:
            raise NvmlError("Invalid Argument")
        return index

    def device_name(self, handle: object) -> str:
        self._check_handle(handle)
        return self.name

    def driver_version(self) -> str:
        return self.driver

    def cuda_driver_version(self) -> int:
        return self.cuda

    def power_usage_mw(self, handle: object) -> int:
        self._check_handle(handle)
        if self.fail_core_reads:
            raise NvmlError("Not Supported")
        return self.power_mw

    def total_energy_mj(self, handle: object) -> int:
        self._check_handle(handle)
        if not self.energy_supported:
            raise NvmlError("Not Supported")
        return self.energy_mj

    def clock_mhz(self, handle: object, clock_type: int) -> int:
        self._check_handle(handle)
        if self.fail_core_reads:
            raise NvmlError("Not Supported")
        if clock_type == 2:
            return self.mem_mhz
        return self.sm_mhz

    def temperature_c(self, handle: object) -> int:
        self._check_handle(handle)
        if self.fail_core_reads:
            raise NvmlError("Not Supported")
        return self.temp_c

    def power_limit_constraints_mw(self, handle: object) -> tuple[int, int]:
        self._check_handle(handle)
        return (self.min_limit_mw, self.max_limit_mw)

    def power_limit_mw(self, handle: object) -> int:
        self._check_handle(handle)
        return self.current_limit_mw

    def power_default_limit_mw(self, handle: object) -> int:
        self._check_handle(handle)
        return self.default_limit_mw

    def set_power_limit_mw(self, handle: object, limit_mw: int) -> None:
        self._check_handle(handle)
        if not self.allow_cap_write:
            raise NvmlError(PERMISSION_DENIED)
        if limit_mw < self.min_limit_mw or limit_mw > self.max_limit_mw:
            raise NvmlError("Invalid Argument")
        self.current_limit_mw = limit_mw
        self.power_history.append(limit_mw)
        self._journal(limit_mw)
        if self.after_set is not None:
            self.after_set(limit_mw)
        if self._raise_after_set:
            self._raise_after_set = False
            raise RuntimeError("boom after write")

    def set_locked_clocks(self, handle: object, min_mhz: int, max_mhz: int) -> None:
        self._check_handle(handle)
        if not self.allow_clock_lock:
            raise NvmlError(PERMISSION_DENIED)
        self.locked = (min_mhz, max_mhz)
        self.lock_calls.append((min_mhz, max_mhz))
        if self._raise_after_lock:
            self._raise_after_lock = False
            raise RuntimeError("boom after lock")

    def reset_locked_clocks(self, handle: object) -> None:
        self._check_handle(handle)
        if self.locked is None and not self.allow_clock_lock:
            raise NvmlError(PERMISSION_DENIED)
        self.locked = None
        self.reset_calls += 1

    def _check_handle(self, handle: object) -> None:
        if self._device_count < 1:
            raise NvmlError("Invalid Argument")
        if handle != 0:
            raise NvmlError("Invalid Argument")

    def _journal(self, limit_mw: int) -> None:
        if self.journal_path is None:
            return
        with self.journal_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{limit_mw}\n")
            handle.flush()
            os.fsync(handle.fileno())
