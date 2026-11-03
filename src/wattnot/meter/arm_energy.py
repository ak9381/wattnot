"""Two-arm GPU energy for the H100 eye and spoken blocks, with no new CLI flags.

The block's normal (gated) run is the "without_wattnot" arm at whatever limit the GPU is on
(stock between blocks). If a best cap is known (/root/run2/best_cap_w, or WATTNOT_BEST_CAP_W),
the same items are answered once more at that cap as the "with_wattnot" arm, capped at
EXTRA_ARM_MAX_S, and the original limit is always restored. Energy is the NVML total-energy
counter around each arm (falling back to power sampling), so it includes idle draw.

Nothing here may fail a block: every error just leaves the energy fields out.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

BEST_CAP_FILE = Path(os.environ.get("WATTNOT_BEST_CAP_FILE", "/root/run2/best_cap_w"))
EXTRA_ARM_MAX_S = 120.0
METHOD = "NVML total-energy counter around the arm (incl. idle draw)"


FALLBACK_CAP_W = 350


def resolve_cap(best_cap: str | int | None = None, cap_source: str | None = None
                ) -> tuple[int | None, str | None]:
    """Pick the with_wattnot cap and say where it came from.

    best_cap is what the driver filled in from this pass's own sweep ("NONE" when the sweep
    had no passing cap). Then we fall back to 350 W. With no best_cap at all (older callers)
    the env var / best_cap_w file is used as before."""
    if best_cap is not None:
        try:
            v = int(float(best_cap))
            if v > 0:
                return v, cap_source or "sweep"
        except (TypeError, ValueError):
            pass
        return FALLBACK_CAP_W, "fallback_350w_no_sweep_winner"
    if os.environ.get("WATTNOT_BEST_CAP_W"):
        c = best_cap_w()
        return c, ("env:WATTNOT_BEST_CAP_W" if c else None)
    c = best_cap_w()
    return c, (f"file:{BEST_CAP_FILE}" if c else None)


def best_cap_w() -> int | None:
    v = os.environ.get("WATTNOT_BEST_CAP_W")
    try:
        if not v and BEST_CAP_FILE.exists():
            v = BEST_CAP_FILE.read_text().strip()
        return int(float(v)) if v else None
    except (OSError, ValueError):
        return None


class Gpu:
    """Thin pynvml wrapper; raises on init failure (callers catch)."""

    def __init__(self, index: int = 0):
        import pynvml  # noqa: PLC0415
        self.nv = pynvml
        pynvml.nvmlInit()
        self.h = pynvml.nvmlDeviceGetHandleByIndex(index)
        self.index = index

    def limit_w(self) -> int:
        return int(self.nv.nvmlDeviceGetPowerManagementLimit(self.h)) // 1000

    def set_limit_w(self, w: int) -> None:
        try:
            self.nv.nvmlDeviceSetPowerManagementLimit(self.h, int(w) * 1000)
        except Exception:  # noqa: BLE001
            subprocess.run(["nvidia-smi", "-i", str(self.index), "-pl", str(int(w))],
                           check=True, capture_output=True, timeout=30)

    def energy_j(self) -> float | None:
        try:
            return self.nv.nvmlDeviceGetTotalEnergyConsumption(self.h) / 1000.0
        except Exception:  # noqa: BLE001
            return None

    def power_w(self) -> float:
        return self.nv.nvmlDeviceGetPowerUsage(self.h) / 1000.0


class ArmMeter:
    """Context manager: .joules after exit (counter, or sampled power as a fallback)."""

    def __init__(self, gpu: Gpu):
        self.gpu, self.joules, self.method = gpu, None, METHOD
        self._stop = threading.Event()
        self._samples: list[tuple[float, float]] = []

    def _sample(self):
        while not self._stop.is_set():
            try:
                self._samples.append((time.monotonic(), self.gpu.power_w()))
            except Exception:  # noqa: BLE001
                pass
            self._stop.wait(0.2)

    def __enter__(self):
        self.t0 = time.monotonic()
        self.e0 = self.gpu.energy_j()
        if self.e0 is None:
            self.method = "sampled NVML power every 0.2 s, integrated (incl. idle draw)"
            self._th = threading.Thread(target=self._sample, daemon=True)
            self._th.start()
        return self

    def __exit__(self, *exc):
        self.wall_s = time.monotonic() - self.t0
        try:
            if self.e0 is not None:
                e1 = self.gpu.energy_j()
                self.joules = (e1 - self.e0) if e1 is not None else None
            else:
                self._stop.set()
                self._th.join(timeout=2)
                s = self._samples
                self.joules = sum((b[0] - a[0]) * (a[1] + b[1]) / 2 for a, b in zip(s, s[1:])) or None
        except Exception:  # noqa: BLE001
            self.joules = None
        return False


def arm_row(meter: ArmMeter | None, *, limit_w, correct: int, items: int, passed, unit: str) -> dict:
    j = meter.joules if meter else None
    return {"limit_w": limit_w, "correct": correct, "items": items, "passed": passed,
            "energy_j": round(j, 1) if j else None,
            "wh": round(j / 3600, 5) if j else None,
            f"j_per_{unit}": round(j / items, 2) if j and items else None,
            "j_per_correct": round(j / correct, 1) if j and correct else None,
            "wall_s": round(meter.wall_s, 2) if meter else None,
            "label": "MEASURED" if j else None,
            "method": meter.method if meter else None}


def summarize(arms: dict, unit: str) -> dict:
    out = {"energy_arms": arms}
    w, o = arms.get("with_wattnot") or {}, arms.get("without_wattnot") or {}
    k = f"j_per_{unit}"
    if w.get(k) and o.get(k):
        out[f"savings_pct_{unit}"] = round(100 * (1 - w[k] / o[k]), 1)
        out["savings_label"] = "MEASURED"
    return out


def two_arms(run_once, *, items: int, unit: str, budget_s: float | None, live: bool,
             best_cap: str | int | None = None, cap_source: str | None = None):
    """run_once(budget_s) -> (result, correct, passed). Returns (first_result, energy_doc|None).

    The first call is the block's real gated run and its result is what the block reports;
    energy failures never change it."""
    gpu = meter = None
    if live:
        try:
            gpu = Gpu()
        except Exception:  # noqa: BLE001
            gpu = None
    t_start = time.monotonic()
    if gpu is None:
        return run_once(budget_s)[0], None
    try:
        lim0 = gpu.limit_w()
        meter = ArmMeter(gpu)
    except Exception:  # noqa: BLE001
        return run_once(budget_s)[0], None
    with meter:
        res, ok, passed = run_once(budget_s)
    try:
        arms = {"without_wattnot": arm_row(meter, limit_w=lim0, correct=ok, items=items,
                                           passed=passed, unit=unit)}
        cap, src = resolve_cap(best_cap, cap_source)
        left = EXTRA_ARM_MAX_S if budget_s is None else min(
            EXTRA_ARM_MAX_S, budget_s * 1.5 - (time.monotonic() - t_start))
        if cap and cap < lim0 and left > 15:
            try:
                gpu.set_limit_w(cap)
                time.sleep(1.0)
                m2 = ArmMeter(gpu)
                with m2:
                    _r2, ok2, passed2 = run_once(left)
                arms["with_wattnot"] = arm_row(m2, limit_w=cap, correct=ok2, items=items,
                                               passed=passed2, unit=unit)
            finally:
                try:
                    gpu.set_limit_w(lim0)
                except Exception:  # noqa: BLE001
                    pass
        doc = summarize(arms, unit)
        doc["cap_w"] = cap if "with_wattnot" in arms else None
        doc["cap_source"] = src
        doc["note"] = ("without_wattnot = the gated run at the GPU's limit then; with_wattnot = "
                       "same items again at cap_w (from cap_source); the block's pass/fail is the "
                       "first run only")
        return res, doc
    except Exception as e:  # noqa: BLE001
        return res, {"energy_error": type(e).__name__}
