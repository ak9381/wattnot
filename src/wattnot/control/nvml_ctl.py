"""Safe access to one GPU's power limit.

`CapGuard` reads the cap on entry and puts it back on exit, on an
exception, and on SIGTERM. Caps are clamped to the driver's min/max, so
the controller can never ask for an out-of-range value.
"""

from __future__ import annotations

import signal
import threading


class CapGuard:
    def __init__(self, nvml, handle, *, handle_sigterm: bool = True) -> None:
        self.nvml = nvml
        self.handle = handle
        lo, hi = nvml.power_limit_constraints_mw(handle)
        self.min_w, self.max_w = lo // 1000, hi // 1000
        self.saved_mw: int | None = None
        self._handle_sigterm = handle_sigterm
        self._old_handler = None

    def __enter__(self) -> CapGuard:
        self.saved_mw = int(self.nvml.power_limit_mw(self.handle))
        if self._handle_sigterm and threading.current_thread() is threading.main_thread():
            self._old_handler = signal.signal(signal.SIGTERM, self._on_term)
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            self.restore()
        finally:
            if self._old_handler is not None:
                signal.signal(signal.SIGTERM, self._old_handler)
                self._old_handler = None

    def _on_term(self, signum, frame) -> None:
        self.restore()
        raise SystemExit(128 + signum)

    def clamp(self, watts: int) -> int:
        return max(self.min_w, min(self.max_w, int(watts)))

    def cap_w(self) -> int:
        return int(self.nvml.power_limit_mw(self.handle)) // 1000

    def set_cap_w(self, watts: int) -> int:
        watts = self.clamp(watts)
        self.nvml.set_power_limit_mw(self.handle, watts * 1000)
        return watts

    def restore(self) -> None:
        if self.saved_mw is not None and self.nvml.power_limit_mw(self.handle) != self.saved_mw:
            self.nvml.set_power_limit_mw(self.handle, self.saved_mw)
