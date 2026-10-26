"""Meter tests. The GPU is the in-memory NVML fake; the clock is injected.

Nothing here opens a driver library or spends a second of real wait on the
30 s and 60 s windows. Sleep moves the fake clock forward.
"""

from __future__ import annotations

import csv
import ctypes
import hashlib
import math
import threading
from pathlib import Path

import pytest
from tests.fixtures.nvml_fake import FakeNVML

from wattnot.meter import NvmlMeter, trace_watt_hours
from wattnot.probe import NoGpu, NvmlError, NvmlUnavailable

TRACE = Path(__file__).resolve().parent / "data" / "trace_h100_sample.csv"
RUN_ID = "run_20261027_050000_ab12cd"
COLUMNS = ["t_s", "gpu", "power_w", "energy_mj", "sm_mhz", "mem_mhz", "temp_c", "util_pct"]


class ManualClock:
    """Clock that moves only when the meter asks to sleep."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self.t += seconds


class CurveNVML(FakeNVML):
    """Power and energy follow one known curve of the injected clock.

    P(t) = 400 + 80 sin(2π t / 5) watts. The sine completes a whole number of
    cycles every 5 s, so its integral over 30 s is exactly 400 W * 30 s.
    """

    def __init__(self, clock: ManualClock) -> None:
        super().__init__(power_mw=0, energy_mj=0)
        self._clock = clock

    def power_usage_mw(self, handle: object) -> int:
        self._check_handle(handle)
        return int(round(self.power_w(self._clock()) * 1000))

    def total_energy_mj(self, handle: object) -> int:
        self._check_handle(handle)
        return int(round(self.energy_j(self._clock()) * 1000))

    @staticmethod
    def power_w(t: float) -> float:
        return 400.0 + 80.0 * math.sin(2.0 * math.pi * t / 5.0)

    @staticmethod
    def energy_j(t: float) -> float:
        # ∫ (400 + 80 sin(2π u / 5)) du from 0 to t.
        return 400.0 * t - (80.0 * 5.0 / (2.0 * math.pi)) * (
            math.cos(2.0 * math.pi * t / 5.0) - 1.0
        )


class BiasedNVML(FakeNVML):
    """Power says 100 W. The counter accumulates at 200 W. They disagree on purpose."""

    def __init__(self, clock: ManualClock) -> None:
        super().__init__(power_mw=100_000, energy_mj=0)
        self._clock = clock

    def power_usage_mw(self, handle: object) -> int:
        self._check_handle(handle)
        return 100_000

    def total_energy_mj(self, handle: object) -> int:
        self._check_handle(handle)
        return int(round(200.0 * self._clock() * 1000))


class RewindingNVML(FakeNVML):
    """Energy counter goes backwards. That reading cannot be a window's Wh."""

    def __init__(self, clock: ManualClock) -> None:
        super().__init__(power_mw=100_000, energy_mj=1_000_000)
        self._clock = clock

    def power_usage_mw(self, handle: object) -> int:
        self._check_handle(handle)
        return 100_000

    def total_energy_mj(self, handle: object) -> int:
        self._check_handle(handle)
        return 1_000_000 - int(self._clock() * 1000)


class TwoGPU(FakeNVML):
    """Two devices, constant power, no energy counter."""

    def __init__(self) -> None:
        super().__init__(device_count=2, energy_supported=False, power_mw=0)

    def device_handle(self, index: int) -> int:
        if index not in (0, 1):
            raise NvmlError("Invalid Argument")
        return index

    def power_usage_mw(self, handle: object) -> int:
        if handle not in (0, 1):
            raise NvmlError("Invalid Argument")
        return 100_000 if handle == 0 else 300_000

    def clock_mhz(self, handle: object, clock_type: int) -> int:
        if handle not in (0, 1):
            raise NvmlError("Invalid Argument")
        return 2619 if clock_type == 2 else 1800

    def temperature_c(self, handle: object) -> int:
        if handle not in (0, 1):
            raise NvmlError("Invalid Argument")
        return 40

    def total_energy_mj(self, handle: object) -> int:
        if handle not in (0, 1):
            raise NvmlError("Invalid Argument")
        raise NvmlError("Not Supported")

    def utilization_pct(self, handle: object) -> int:
        if handle not in (0, 1):
            raise NvmlError("Invalid Argument")
        return 10 if handle == 0 else 70


def _meter(fake, tmp_path, clock: ManualClock | None = None, **kwargs) -> NvmlMeter:
    if clock is None:
        clock = ManualClock()
    return NvmlMeter(
        fake,
        run_id=kwargs.pop("run_id", RUN_ID),
        traces_root=tmp_path,
        clock=clock,
        sleep=clock.sleep,
        **kwargs,
    )


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == COLUMNS
        return list(reader)


def _trap_wh(rows: list[dict[str, str]], gpu: str | None = None) -> float:
    """Trapezoid Wh from CSV power, written out here so it is not the meter's own helper."""
    chosen = [row for row in rows if gpu is None or row["gpu"] == gpu]
    joules = 0.0
    for left, right in zip(chosen, chosen[1:], strict=False):
        dt = float(right["t_s"]) - float(left["t_s"])
        joules += (float(left["power_w"]) + float(right["power_w"])) * 0.5 * dt
    return joules / 3600.0


def _counter_wh(rows: list[dict[str, str]], gpu: str = "0") -> float:
    chosen = [row for row in rows if row["gpu"] == gpu]
    return (int(chosen[-1]["energy_mj"]) - int(chosen[0]["energy_mj"])) / 3_600_000


def test_sample_trace_matches_hand_calculation():
    # 400 W held for 1.0 s is 400 J. The counter moves 0 -> 400_000 mJ.
    # 1 Wh = 3_600_000 mJ, so the window is 400_000 / 3_600_000 Wh.
    rows = _rows(TRACE)
    assert float(rows[0]["t_s"]) == 0.0
    assert float(rows[-1]["t_s"]) == 1.0
    assert int(rows[0]["energy_mj"]) == 0
    assert int(rows[-1]["energy_mj"]) == 400_000
    assert all(float(row["power_w"]) == 400 for row in rows)
    assert [float(row["t_s"]) for row in rows] == [i / 10 for i in range(11)]

    wh, method = trace_watt_hours(TRACE)
    assert method == "counter"
    assert wh == pytest.approx(400_000 / 3_600_000)
    assert wh == pytest.approx(400 / 3600)
    # Constant power, so the trapezoid of the same rows is the same 400 J.
    assert wh == pytest.approx(_trap_wh(rows))


def test_energy_counter_is_used_when_it_disagrees_with_power(tmp_path):
    # Power column is 0 W, so a trapezoid is 0. The counter moves 1 Wh.
    path = tmp_path / "bias.csv"
    path.write_text(
        "t_s,gpu,power_w,energy_mj,sm_mhz,mem_mhz,temp_c,util_pct\n"
        "0,0,0,0,1800,2619,40,\n"
        "1,0,0,3600000,1800,2619,40,\n",
        encoding="utf-8",
    )
    wh, method = trace_watt_hours(path)
    assert method == "counter"
    assert wh == pytest.approx(1.0)


def test_missing_energy_column_uses_trapezoid(tmp_path):
    # (100 + 300) / 2 * 1 s + (300 + 100) / 2 * 1 s = 400 J.
    path = tmp_path / "trap.csv"
    path.write_text(
        "t_s,gpu,power_w,energy_mj,sm_mhz,mem_mhz,temp_c,util_pct\n"
        "0,0,100,,1800,2619,40,\n"
        "1,0,300,,1800,2619,40,\n"
        "2,0,100,,1800,2619,40,\n",
        encoding="utf-8",
    )
    wh, method = trace_watt_hours(path)
    assert method == "trapezoid"
    assert wh == pytest.approx(400 / 3600)


def test_counter_and_trapezoid_agree_on_a_known_curve(tmp_path):
    clock = ManualClock()
    fake = CurveNVML(clock)
    meter = _meter(fake, tmp_path, clock)
    meter.start("curve_1", seconds=30)
    window = meter.stop()

    rows = _rows(Path(window.csv_path))
    counter = _counter_wh(rows)
    trap = _trap_wh(rows)
    assert counter == pytest.approx(trap, rel=0.01)
    # 30 s is 6 full periods of the sine, so the integral is exactly 400 W * 30 s.
    assert counter == pytest.approx(400.0 * 30.0 / 3600.0, rel=1e-6)
    assert window.wh == pytest.approx(counter, rel=1e-9)
    assert window.wh == pytest.approx(trace_watt_hours(window.csv_path)[0])
    assert window.method == "counter"
    assert window.label == "MEASURED"
    assert window.source == "nvml"
    assert window.seconds == pytest.approx(30.0)
    assert window.mean_w == pytest.approx(window.wh * 3600.0 / window.seconds)
    assert fake.power_history == []
    assert fake.shutdown_called is False


def test_sample_rate_over_30s_on_the_fake_clock(tmp_path):
    clock = ManualClock()
    meter = _meter(FakeNVML(), tmp_path, clock)
    meter.start("rate_1", seconds=30)
    window = meter.stop()

    rows = _rows(Path(window.csv_path))
    times = [float(row["t_s"]) for row in rows]
    elapsed = times[-1] - times[0]
    assert elapsed == pytest.approx(30.0)
    rate = (len(times) - 1) / elapsed
    assert rate >= 9.5
    assert rate == pytest.approx(10.0, abs=0.01)
    assert len(times) == 301  # t = 0, 0.1, ..., 30.0
    assert not any(item.name == "wattnot-meter" for item in threading.enumerate())


def test_one_hertz_samples_are_downsampled(tmp_path):
    events: list[dict] = []
    clock = ManualClock()
    meter = _meter(FakeNVML(power_mw=250_000), tmp_path, clock, on_sample=events.append)
    meter.start("ui_1", seconds=30)
    window = meter.stop()

    assert len(events) == 31  # t = 0, 1, 2, ..., 30
    assert len(events) < len(_rows(Path(window.csv_path)))
    sample = events[0]
    assert set(sample) == {
        "gpu_index",
        "power_w",
        "energy_mj",
        "sm_clock_mhz",
        "mem_clock_mhz",
        "temp_c",
        "util_pct",
        "source",
    }
    assert sample["gpu_index"] == 0
    assert sample["power_w"] == pytest.approx(250.0)
    assert sample["energy_mj"] == 1_000_000
    assert sample["sm_clock_mhz"] == 1800
    assert sample["mem_clock_mhz"] == 2619
    assert sample["temp_c"] == 42
    assert sample["util_pct"] is None
    assert sample["source"] == "nvml"
    assert events[1]["power_w"] == pytest.approx(250.0)


def test_csv_hash_recomputes(tmp_path):
    clock = ManualClock()
    meter = _meter(FakeNVML(), tmp_path, clock)
    meter.start("baseline_1", seconds=1)
    window = meter.stop()

    path = Path(window.csv_path)
    assert path == tmp_path / RUN_ID / "baseline_1.csv"
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    assert window.sha256 == digest
    sidecar = path.with_name(path.name + ".sha256")
    assert sidecar.read_text(encoding="utf-8").strip() == digest
    assert sidecar.read_text(encoding="utf-8") == digest + "\n"
    assert data.decode("utf-8").splitlines()[0] == ",".join(COLUMNS)
    assert window.csv_path == str(path)


def test_counter_preferred_when_power_disagrees(tmp_path):
    clock = ManualClock()
    meter = _meter(BiasedNVML(clock), tmp_path, clock)
    meter.start("bias_1", seconds=2)
    window = meter.stop()

    # Counter: 200 W * 2 s = 400 J. Trapezoid of the 100 W samples is 200 J.
    assert window.wh == pytest.approx(400 / 3600)
    assert window.wh != pytest.approx(_trap_wh(_rows(Path(window.csv_path))))
    assert window.method == "counter"
    assert window.label == "MEASURED"
    assert window.source == "nvml"


def test_missing_counter_falls_back_to_trapezoid(tmp_path):
    clock = ManualClock()
    fake = FakeNVML(power_mw=180_000, energy_supported=False)
    meter = _meter(fake, tmp_path, clock)
    meter.start("fallback_1", seconds=2)
    window = meter.stop()

    rows = _rows(Path(window.csv_path))
    assert rows[0]["energy_mj"] == ""
    assert window.method == "trapezoid"
    assert window.label == "ESTIMATE"
    assert window.source == "nvml"
    assert window.wh == pytest.approx(_trap_wh(rows))
    assert window.wh == pytest.approx(180.0 * 2.0 / 3600.0)
    assert window.mean_w == pytest.approx(180.0)


def test_backwards_counter_falls_back_to_trapezoid(tmp_path):
    clock = ManualClock()
    meter = _meter(RewindingNVML(clock), tmp_path, clock)
    meter.start("rewind_1", seconds=1)
    window = meter.stop()

    assert window.method == "trapezoid"
    assert window.label == "ESTIMATE"
    assert window.wh == pytest.approx(100.0 / 3600.0)


def test_stuck_counter_is_not_reported_as_measured_zero(tmp_path):
    # The fake's counter stays at its boot value while the GPU draws 200 W.
    clock = ManualClock()
    fake = FakeNVML(power_mw=200_000, energy_mj=5_000_000, energy_supported=True)
    meter = _meter(fake, tmp_path, clock)
    meter.start("stuck_1", seconds=2)
    window = meter.stop()

    assert window.method == "trapezoid"
    assert window.label == "ESTIMATE"
    assert window.wh == pytest.approx(200.0 * 2.0 / 3600.0)


def test_idle_default_is_60s_and_a_second_window_gets_its_own_file(tmp_path):
    clock = ManualClock()
    fake = FakeNVML(power_mw=123_000, energy_supported=False)
    meter = _meter(fake, tmp_path, clock)

    mean = meter.idle()
    again = meter.idle(seconds=1)

    assert mean == pytest.approx(123.0)
    assert again == pytest.approx(123.0)
    first = _rows(tmp_path / RUN_ID / "idle_1.csv")
    second = _rows(tmp_path / RUN_ID / "idle_2.csv")
    assert float(first[-1]["t_s"]) == pytest.approx(60.0)
    assert float(second[-1]["t_s"]) == pytest.approx(1.0)
    assert float(first[1]["t_s"]) - float(first[0]["t_s"]) == pytest.approx(0.1)
    assert (tmp_path / RUN_ID / "idle_1.csv.sha256").exists()
    assert fake.power_history == []


def test_each_gpu_is_sampled_and_util_is_recorded(tmp_path):
    clock = ManualClock()
    fake = TwoGPU()
    meter = _meter(fake, tmp_path, clock)
    meter.start("gpus_1", seconds=1)
    window = meter.stop()

    rows = _rows(Path(window.csv_path))
    gpu0 = [row for row in rows if row["gpu"] == "0"]
    gpu1 = [row for row in rows if row["gpu"] == "1"]
    assert len(gpu0) == len(gpu1) == 11
    assert {row["util_pct"] for row in gpu0} == {"10"}
    assert {row["util_pct"] for row in gpu1} == {"70"}
    # 100 W + 300 W for 1 s = 400 J, split across the two devices.
    assert window.wh == pytest.approx(400 / 3600)
    assert window.mean_w == pytest.approx(400.0)
    assert window.label == "ESTIMATE"
    assert _trap_wh(rows, "0") == pytest.approx(100 / 3600)
    assert _trap_wh(rows, "1") == pytest.approx(300 / 3600)


def test_start_stop_without_a_bound_still_hashes(tmp_path):
    clock = ManualClock()
    meter = _meter(FakeNVML(energy_supported=False), tmp_path, clock)
    meter.start("rep_1")
    try:
        assert meter.running
        alive = [item for item in threading.enumerate() if item.name == "wattnot-meter"]
        assert len(alive) == 1
        assert alive[0].is_alive()
        assert alive[0] is not threading.current_thread()
    finally:
        window = meter.stop()
    assert not meter.running
    assert not alive[0].is_alive()
    data = Path(window.csv_path).read_bytes()
    assert hashlib.sha256(data).hexdigest() == window.sha256
    assert len(_rows(Path(window.csv_path))) >= 1


def test_no_gpu_raises_before_a_file_is_written(tmp_path):
    fake = FakeNVML(device_count=0)
    meter = _meter(fake, tmp_path)
    with pytest.raises(NoGpu):
        meter.start("rep_1")
    assert list(tmp_path.rglob("*.csv")) == []
    assert fake.shutdown_called is False


def test_stop_without_start_and_bad_labels(tmp_path):
    meter = _meter(FakeNVML(), tmp_path)
    with pytest.raises(RuntimeError):
        meter.stop()
    for label in ("", "../escape", "a/b", "a\\b", ".", ".."):
        with pytest.raises(ValueError):
            meter.start(label)
    assert list(tmp_path.rglob("*")) == []


def test_second_start_is_rejected_until_stop(tmp_path):
    clock = ManualClock()
    meter = _meter(FakeNVML(), tmp_path, clock)
    meter.start("rep_1", seconds=1)
    with pytest.raises(RuntimeError):
        meter.start("rep_2")
    meter.stop()
    assert not meter.running


def test_open_refuses_while_pytest_is_running():
    with pytest.raises(NvmlUnavailable):
        NvmlMeter.open(RUN_ID)


def test_meter_does_not_load_a_driver_library(monkeypatch, tmp_path):
    def boom(*_args, **_kwargs):
        raise AssertionError("CDLL")

    monkeypatch.setattr(ctypes, "CDLL", boom)
    clock = ManualClock()
    meter = _meter(FakeNVML(), tmp_path, clock)
    meter.start("rep_1", seconds=0.2)
    window = meter.stop()
    assert window.source == "nvml"
    assert Path(window.csv_path).exists()
