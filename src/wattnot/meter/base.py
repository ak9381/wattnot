"""10 Hz GPU trace: sample, integrate, write a CSV and its SHA-256.

Wh comes from the hardware energy counter when that counter moves during
the window. Otherwise power samples are integrated with the trapezoid rule
and the window is labelled ESTIMATE. Sampling runs on a background thread
so a trial can use the GPU at the same time.

Tests pass `clock` and `sleep`. Sleep on the fake clock returns at once
and moves the clock forward, so a 60 s idle window does not wait 60 s.
"""

from __future__ import annotations

import csv
import hashlib
import os
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# NVML's total-energy counter is in millijoules. 1 Wh = 3600 J = 3_600_000 mJ.
MJ_PER_WH = 3_600_000
SAMPLE_HZ = 10.0
COLUMNS = ["t_s", "gpu", "power_w", "energy_mj", "sm_mhz", "mem_mhz", "temp_c", "util_pct"]

_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SampleCallback = Callable[[dict], None]


@dataclass(frozen=True)
class Reading:
    """One row of a trace, before it is written to CSV."""

    t_s: float
    gpu: int
    power_w: float
    energy_mj: int | None
    sm_mhz: int
    mem_mhz: int
    temp_c: int
    util_pct: int | None


@dataclass(frozen=True)
class GpuSample:
    """What a backend returns for one device at one instant. Time is added by the sampler."""

    gpu: int
    power_w: float
    energy_mj: int | None
    sm_mhz: int
    mem_mhz: int
    temp_c: int
    util_pct: int | None


@dataclass(frozen=True)
class Window:
    """One finished measurement.

    `source` is the backend name (`nvml`, `dcgm`, `zeus`). `label` is
    MEASURED when `wh` came from the energy counter and ESTIMATE when it
    was integrated from power. `method` is `counter` or `trapezoid`.
    """

    wh: float
    seconds: float
    mean_w: float
    csv_path: str
    sha256: str
    source: str
    label: str
    method: str


class Meter(Protocol):
    """Start a window, stop it, or measure an idle stretch."""

    def start(self, label: str) -> None:
        """Begin sampling. `label` becomes the CSV file name."""

    def stop(self) -> Window:
        """Stop sampling, write the CSV and its SHA-256, and return the window."""

    def idle(self, seconds: int = 60) -> float:
        """Mean watts over a quiet window. The default length is 60 s."""


def trace_watt_hours(path: str | Path) -> tuple[float, str]:
    """Wh stored in a trace CSV, and the method that produced it.

    The method is `counter` when every selected row has an `energy_mj` value
    and the counter moved forward (or the power integral is zero). Otherwise
    it is `trapezoid`. Per-GPU results are summed.
    """

    return integrate(read_trace(Path(path)))


def integrate(readings: Sequence[Reading]) -> tuple[float, str]:
    """Sum Wh across GPUs. Prefer each GPU's counter; fall back per device."""

    if not readings:
        return 0.0, "trapezoid"
    by_gpu: dict[int, list[Reading]] = {}
    for reading in readings:
        by_gpu.setdefault(reading.gpu, []).append(reading)
    total = 0.0
    methods: list[str] = []
    for rows in by_gpu.values():
        ordered = sorted(rows, key=lambda item: item.t_s)
        wh, method = _one_gpu(ordered)
        total += wh
        methods.append(method)
    if methods and all(method == "counter" for method in methods):
        return total, "counter"
    return total, "trapezoid"


def read_trace(path: Path) -> list[Reading]:
    """Read a trace CSV. Blank `energy_mj` or `util_pct` becomes None."""

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if list(reader.fieldnames or []) != COLUMNS:
            found = reader.fieldnames
            raise ValueError(f"{path} columns are {found!r}, expected {COLUMNS}")
        return [_reading_from_row(row) for row in reader]


def write_trace(path: Path, readings: Sequence[Reading]) -> str:
    """Write the CSV and a `<name>.csv.sha256` file next to it. Return the hex digest."""

    data = _csv_text(readings).encode("utf-8")
    _fsync_write(path, data)
    digest = hashlib.sha256(data).hexdigest()
    _fsync_write(path.with_name(path.name + ".sha256"), (digest + "\n").encode("utf-8"))
    return digest


class SamplingMeter:
    """Background sampler shared by the NVML backend (and later DCGM / Zeus).

    `start(label)` samples until `stop`. `start(label, seconds=N)` samples
    for N seconds of `clock` and `stop` waits for that bound instead of
    cutting the window short. `idle` is a bounded window and returns mean watts.
    """

    source = "nvml"

    def __init__(
        self,
        *,
        run_id: str,
        traces_root: str | Path = "traces",
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        hz: float = SAMPLE_HZ,
        on_sample: _SampleCallback | None = None,
    ) -> None:
        if hz <= 0:
            raise ValueError("hz must be positive")
        _check_run_id(run_id)
        self.run_id = run_id
        self.traces_root = Path(traces_root)
        self.hz = float(hz)
        self._clock = clock or time.perf_counter
        self._sleep = sleep or time.sleep
        self._on_sample = on_sample
        self._readings: list[Reading] = []
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._started = threading.Event()
        self._error: Exception | None = None
        self._running = False
        self._limit_s: float | None = None
        self._label = ""
        self._idle_i = 0

    @property
    def running(self) -> bool:
        return self._running

    def start(self, label: str, *, seconds: float | None = None) -> None:
        """Start the sampler thread. See the class docstring for `seconds`."""

        if self._running:
            raise RuntimeError("meter is already running")
        _check_label(label)
        if seconds is not None and seconds < 0:
            raise ValueError("seconds must be >= 0")
        self._label = label
        self._limit_s = None if seconds is None else float(seconds)
        self._readings = []
        self._error = None
        self._stop_event = threading.Event()
        self._started = threading.Event()
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="wattnot-meter", daemon=True)
        self._thread.start()
        if not self._started.wait(timeout=5):
            self._stop_event.set()
            self._thread.join(timeout=1)
            self._running = False
            raise RuntimeError("meter sampler did not start")
        if self._error is not None and not self._readings:
            self._thread.join(timeout=1)
            self._running = False
            err = self._error
            self._error = None
            raise err

    def stop(self) -> Window:
        """End the window. A bounded `start` is allowed to finish first."""

        if not self._running or self._thread is None:
            raise RuntimeError("meter is not running")
        if self._limit_s is None:
            self._stop_event.set()
        self._thread.join(timeout=self._join_timeout())
        if self._thread.is_alive():
            self._stop_event.set()
            self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise RuntimeError("meter sampler did not stop")
        self._running = False
        if self._error is not None:
            err = self._error
            self._error = None
            raise err
        if not self._readings:
            raise RuntimeError("meter recorded no samples")
        return self._finalize()

    def idle(self, seconds: int = 60) -> float:
        """Mean watts over `seconds` of idle. Writes `idle_<n>.csv`."""

        if seconds < 0:
            raise ValueError("idle seconds must be >= 0")
        self._idle_i += 1
        self.start(f"idle_{self._idle_i}", seconds=float(seconds))
        return self.stop().mean_w

    def _prepare(self) -> None:
        """Backend hook. Open devices before the first sample."""

    def _sample_gpus(self) -> list[GpuSample]:
        """Backend hook. One sample per GPU, taken back to back."""

        raise NotImplementedError

    def _loop(self) -> None:
        try:
            self._prepare()
            origin = self._clock()
            period = 1.0 / self.hz
            limit = self._limit_s
            index = 0
            next_emit = 0.0
            while True:
                if self._stop_event.is_set() and limit is None:
                    break
                target = origin + index * period
                if limit is not None and target > origin + limit + 1e-9:
                    break
                delay = target - self._clock()
                if delay > 0:
                    self._pause(delay)
                    if self._stop_event.is_set() and limit is None:
                        break
                t_s = self._clock() - origin
                batch = self._take(t_s)
                self._readings.extend(batch)
                if index == 0:
                    self._started.set()
                next_emit = self._emit(batch, t_s, next_emit)
                index += 1
                if limit is not None and t_s + 1e-9 >= limit:
                    break
                time.sleep(0)
        except Exception as exc:
            self._error = exc
            self._started.set()

    def _take(self, t_s: float) -> list[Reading]:
        return [
            Reading(
                t_s=t_s,
                gpu=sample.gpu,
                power_w=sample.power_w,
                energy_mj=sample.energy_mj,
                sm_mhz=sample.sm_mhz,
                mem_mhz=sample.mem_mhz,
                temp_c=sample.temp_c,
                util_pct=sample.util_pct,
            )
            for sample in self._sample_gpus()
        ]

    def _emit(self, batch: list[Reading], t_s: float, next_emit: float) -> float:
        """Hand the UI one sample per GPU per elapsed second, not the full 10 Hz."""

        if self._on_sample is None or t_s + 1e-9 < next_emit:
            return next_emit
        for reading in batch:
            self._on_sample(
                {
                    "gpu_index": reading.gpu,
                    "power_w": reading.power_w,
                    "energy_mj": reading.energy_mj,
                    "sm_clock_mhz": reading.sm_mhz,
                    "mem_clock_mhz": reading.mem_mhz,
                    "temp_c": reading.temp_c,
                    "util_pct": reading.util_pct,
                    "source": self.source,
                }
            )
        updated = next_emit
        while updated <= t_s + 1e-9:
            updated += 1.0
        return updated

    def _pause(self, delay: float) -> None:
        # Real time is slept in short slices so stop() lands quickly.
        # A fake clock must see one sleep of the full delay, or the window drifts.
        if self._sleep is time.sleep:
            remaining = delay
            while remaining > 0:
                if self._stop_event.is_set() and self._limit_s is None:
                    return
                step = min(remaining, 0.05)
                self._sleep(step)
                remaining -= step
            return
        self._sleep(delay)
        time.sleep(0)

    def _join_timeout(self) -> float:
        if self._sleep is not time.sleep:
            return 5.0
        return (self._limit_s or 0.0) + 5.0

    def _finalize(self) -> Window:
        readings = sorted(self._readings, key=lambda item: (item.t_s, item.gpu))
        wh, method = integrate(readings)
        seconds = readings[-1].t_s - readings[0].t_s
        if seconds > 0:
            mean_w = wh * 3600.0 / seconds
        else:
            latest: dict[int, float] = {}
            for reading in readings:
                latest[reading.gpu] = reading.power_w
            mean_w = float(sum(latest.values()))
        path = self.traces_root / self.run_id / f"{self._label}.csv"
        digest = write_trace(path, readings)
        return Window(
            wh=wh,
            seconds=seconds,
            mean_w=mean_w,
            csv_path=str(path),
            sha256=digest,
            source=self.source,
            label="MEASURED" if method == "counter" else "ESTIMATE",
            method=method,
        )


def _one_gpu(rows: list[Reading]) -> tuple[float, str]:
    trap = _trapezoid(rows)
    energies = [row.energy_mj for row in rows]
    if any(value is None for value in energies):
        return trap, "trapezoid"
    first = energies[0]
    last = energies[-1]
    if first is None or last is None:
        return trap, "trapezoid"
    if last < first:
        return trap, "trapezoid"
    # A counter that sits still while the GPU is drawing power is not measuring.
    if last == first and trap > 0:
        return trap, "trapezoid"
    return (last - first) / MJ_PER_WH, "counter"


def _trapezoid(rows: Sequence[Reading]) -> float:
    joules = 0.0
    for left, right in zip(rows, rows[1:], strict=False):
        dt = right.t_s - left.t_s
        if dt < 0:
            raise ValueError("samples go backwards in time")
        joules += (left.power_w + right.power_w) * 0.5 * dt
    return joules / 3600.0


def _reading_from_row(row: dict[str, str]) -> Reading:
    return Reading(
        t_s=float(row["t_s"]),
        gpu=int(row["gpu"]),
        power_w=float(row["power_w"]),
        energy_mj=_blank_int(row["energy_mj"]),
        sm_mhz=int(row["sm_mhz"]),
        mem_mhz=int(row["mem_mhz"]),
        temp_c=int(row["temp_c"]),
        util_pct=_blank_int(row["util_pct"]),
    )


def _blank_int(text: str) -> int | None:
    stripped = text.strip()
    if stripped == "":
        return None
    return int(stripped)


def _csv_text(readings: Sequence[Reading]) -> str:
    lines = [",".join(COLUMNS)]
    for reading in readings:
        energy = "" if reading.energy_mj is None else str(int(reading.energy_mj))
        util = "" if reading.util_pct is None else str(int(reading.util_pct))
        lines.append(
            ",".join(
                [
                    f"{reading.t_s:.6f}",
                    str(int(reading.gpu)),
                    f"{reading.power_w:.3f}",
                    energy,
                    str(int(reading.sm_mhz)),
                    str(int(reading.mem_mhz)),
                    str(int(reading.temp_c)),
                    util,
                ]
            )
        )
    return "\n".join(lines) + "\n"


def _fsync_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _check_label(label: str) -> None:
    if not _LABEL_RE.fullmatch(label):
        raise ValueError(f"label {label!r} is not a safe file name")


def _check_run_id(run_id: str) -> None:
    if not run_id or run_id in {".", ".."} or "/" in run_id or "\\" in run_id:
        raise ValueError(f"run_id {run_id!r} is not a safe directory name")
