"""Flex on a real GPU (the H100 run). Not used by the offline demo and never run in tests
against a real driver.

The load generator writes a progress file, one JSON object per line, for example
`{"p99_ms": 2310.5, "pass_rate": 0.81, "rps": 9.8}`. Each tick reads the last line.
Power comes from NVML. The power limit is held by `CapGuard`, which puts the old limit back
on exit, on an error and on SIGTERM.

Needs permission to set the power limit (root, or `nvidia-smi -pm 1` and a capable user).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

from wattnot.control.nvml_ctl import CapGuard
from wattnot.flex.controller import FlexConfig
from wattnot.flex.run import FlexRun, HardwareBackend, run_flex
from wattnot.flex.signal import ShedEvent


class ProgressFile:
    """Reads the newest line of the load generator's progress file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._last: dict = {}

    def latest(self) -> dict:
        try:
            lines = self.path.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            lines = []
        for line in reversed(lines):  # a half-written last line is skipped
            try:
                doc = json.loads(line)
            except ValueError:
                continue
            if isinstance(doc, dict):
                self._last = doc
                break
        if not {"p99_ms", "pass_rate"} <= self._last.keys():
            raise RuntimeError(f"no p99_ms/pass_rate yet in {self.path}")
        return self._last


def run_on_gpu(nvml, event: ShedEvent, progress: str | Path, *, gpu: int = 0,
               cfg: FlexConfig | None = None,
               sleep: Callable[[float], None] = time.sleep) -> FlexRun:
    handle = nvml.device_handle(gpu)
    pf = ProgressFile(progress)
    with CapGuard(nvml, handle) as guard:
        backend = HardwareBackend(
            nvml, handle, guard, read_p99_ms=lambda: pf.latest()["p99_ms"],
            read_pass_rate=lambda: pf.latest()["pass_rate"],
            read_rps=lambda: pf.latest().get("rps", 0.0))
        return run_flex(backend, event, cfg, sleep=sleep)
