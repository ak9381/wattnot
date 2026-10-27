"""Safe access to one GPU's power limit.

`CapGuard` reads the cap on entry and puts it back on exit, on an
exception, and on SIGTERM. Caps are clamped to the driver's min/max, so
the controller can never ask for an out-of-range value.

Optional `permit(watts)` is called before every write that is not a restore. It raises
(`wattnot.approvals.NotApproved`) when the change is outside what a human approved, and then
nothing is written. Restoring the saved limit never needs permission.
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable


class CapGuard:
    def __init__(self, nvml, handle, *, handle_sigterm: bool = True,
                 permit: Callable[[int], None] | None = None) -> None:
        self.nvml = nvml
        self.handle = handle
        self.permit = permit
        lo, hi = nvml.power_limit_constraints_mw(handle)
        self.min_w, self.max_w = lo // 1000, hi // 1000
        self.ceiling_w: int | None = None
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

    @property
    def top_w(self) -> int:
        """Highest cap a normal write may use: the driver maximum, or a lower outside ceiling."""
        return self.max_w if self.ceiling_w is None else min(self.max_w, self.ceiling_w)

    def tighten_max(self, watts: int) -> int:
        """Set an outside ceiling (for example the rack budget) below the driver maximum.

        Normal writes are clamped to it. Putting a saved limit back is not: restoring may need
        a value above the ceiling, and it is the state the card started in. Returns `top_w`.
        """
        self.ceiling_w = max(self.min_w, min(self.max_w, int(watts)))
        return self.top_w

    def clamp(self, watts: int, *, restoring: bool = False) -> int:
        hi = self.max_w if restoring else self.top_w
        return max(self.min_w, min(hi, int(watts)))

    def cap_w(self) -> int:
        return int(self.nvml.power_limit_mw(self.handle)) // 1000

    def set_cap_w(self, watts: int, *, restoring: bool = False) -> int:
        """Write a power limit. `restoring=True` (putting a limit back) skips the permit."""
        watts = self.clamp(watts, restoring=restoring)
        if self.permit is not None and not restoring:
            self.permit(watts)
        self.nvml.set_power_limit_mw(self.handle, watts * 1000)
        return watts

    def restore(self) -> None:
        if self.saved_mw is not None and self.nvml.power_limit_mw(self.handle) != self.saved_mw:
            self.nvml.set_power_limit_mw(self.handle, self.saved_mw)
