"""Host (CPU package + DRAM) energy through Linux RAPL powercap.

With expert offload a lot of each token runs on the CPU, so GPU watts alone can
fall while total watts do not. We read RAPL when the VM exposes it. When it
does not, the try is labelled "GPU-only ESTIMATE" and no saving is counted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

POWERCAP = Path("/sys/class/powercap")
GPU_ONLY = "GPU-only ESTIMATE"


@dataclass(frozen=True)
class HostReading:
    joules: float | None
    zones: tuple[str, ...]


class Rapl:
    """Sum `energy_uj` over top-level package zones (their dram subzones are inside)."""

    def __init__(self, root: str | Path = POWERCAP) -> None:
        self.root = Path(root)
        self.zones = sorted(p for p in self.root.glob("intel-rapl:*")
                            if p.name.count(":") == 1 and (p / "energy_uj").is_file())

    @property
    def available(self) -> bool:
        if not self.zones:
            return False
        try:
            for z in self.zones:
                int((z / "energy_uj").read_text())
            return True
        except (OSError, ValueError):
            return False

    def _max(self, z: Path) -> int:
        try:
            return int((z / "max_energy_range_uj").read_text())
        except (OSError, ValueError):
            return 0

    def read(self) -> dict[str, int]:
        return {z.name: int((z / "energy_uj").read_text()) for z in self.zones}

    def joules_between(self, a: dict[str, int], b: dict[str, int]) -> float:
        total = 0
        for z in self.zones:
            d = b[z.name] - a[z.name]
            if d < 0:  # counter wrapped
                d += self._max(z)
            total += d
        return total / 1e6


def boundary(rapl_ok: bool) -> str:
    return "GPU+host" if rapl_ok else GPU_ONLY
