"""SM clock locks (`nvidia-smi -lgc`, via nvmlDeviceSetGpuLockedClocks) with a caps-only fallback.

No provider has proven that `-lgc` works on its VMs (E2E documents `-pl` only). So a clock lock
is always optional: the first failure marks clocks unavailable for the whole process, resets the
card to driver clocks, and every later request runs caps-only. The step records what was asked,
what was applied and why it fell back, so a receipt never claims a clock that was not set.
"""

from __future__ import annotations

# H100 SXM: max SM clock 1980 MHz (RUN2 ran at 1980 under load). Fixed points, not ranges, so a
# "locked" trial means one known clock.
PROFILES: dict[str, tuple[int, int] | None] = {
    "base": None,
    "locked-high": (1755, 1755),
    "locked-mid": (1590, 1590),
    "locked-low": (1410, 1410),
}


class ClockLock:
    def __init__(self, nvml, handle) -> None:
        self.nvml, self.handle = nvml, handle
        self.supported = hasattr(nvml, "set_locked_clocks") and hasattr(nvml, "reset_locked_clocks")
        self.available: bool | None = None if self.supported else False
        self.reason: str | None = None if self.supported else "driver wrapper has no clock lock"
        self.current = "base"

    def apply(self, profile: str | None) -> bool:
        """Lock to `profile`. True when applied (or nothing to do for base); False = caps-only."""
        profile = profile or "base"
        if profile not in PROFILES:
            raise ValueError(f"unknown clock profile {profile!r}")
        rng = PROFILES[profile]
        if rng is None:
            self.reset()
            return True
        if self.available is False:
            return False
        try:
            self.nvml.set_locked_clocks(self.handle, *rng)
            self.available, self.current = True, profile
            return True
        except Exception as e:  # noqa: BLE001 - any refusal means caps-only from now on
            self.available = False
            self.reason = f"{type(e).__name__}: {str(e)[:120]}"
            self.reset()
            return False

    def reset(self) -> None:
        if self.current != "base" and self.supported:
            try:
                self.nvml.reset_locked_clocks(self.handle)
            except Exception:  # noqa: BLE001 - best effort; the driver resets on reboot
                pass
        self.current = "base"


class ClockedRig:
    """A Rig view for one step: the cap arm runs at `profile`, the stock arm at driver clocks."""

    def __init__(self, rig, lock: ClockLock, profile: str) -> None:
        self._rig, self._lock, self._profile = rig, lock, profile
        self.applied: bool = True

    def __getattr__(self, k):
        return getattr(self._rig, k)

    def block(self, cap_w: int, **kw):
        if int(cap_w) == int(self._rig.default_w):
            self._lock.reset()
        elif not self._lock.apply(self._profile):
            self.applied = False
        return self._rig.block(cap_w, **kw)
