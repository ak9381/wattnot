"""Attribution and the DCGM / Zeus stub backends. Offline, fake clock."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from wattnot.meter import BACKENDS, DcgmMeter, ZeusMeter, attribute, open_meter, split_wh
from wattnot.meter.base import Window, trace_watt_hours
from wattnot.meter.dcgm import DcgmUnavailable
from wattnot.meter.zeus import ZeusUnavailable

RUN_ID = "run_20261027_000000_abcdef"


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        if s > 0:
            self.t += s


def _window(wh: float) -> Window:
    return Window(wh, 10.0, wh * 360, "x.csv", "0" * 64, "nvml", "MEASURED", "counter")


def test_shares_sum_to_window():
    out = attribute(_window(12.0), [100, 200, 300, 400], cap=0.5)
    assert sum(out) == pytest.approx(12.0)
    assert out == pytest.approx([1.2, 2.4, 3.6, 4.8])


def test_cap_respected_and_excess_redistributed():
    out = split_wh(10.0, [900, 50, 50], cap=0.5)
    assert sum(out) == pytest.approx(10.0)
    assert max(out) <= 5.0 + 1e-9
    assert out[0] == pytest.approx(5.0)
    assert out[1] == pytest.approx(out[2]) == pytest.approx(2.5)


def test_cascading_cap():
    out = split_wh(10.0, [1000, 900, 1, 1, 1], cap=0.3)
    assert sum(out) == pytest.approx(10.0)
    assert all(x <= 3.0 + 1e-9 for x in out)


def test_infeasible_cap_falls_back_to_equal():
    assert split_wh(9.0, [10, 1, 1], cap=0.1) == pytest.approx([3.0, 3.0, 3.0])


def test_zero_tokens_and_edges():
    assert split_wh(4.0, [0, 0]) == pytest.approx([2.0, 2.0])
    assert split_wh(4.0, []) == []
    assert split_wh(0.0, [1, 2]) == [0.0, 0.0]
    with pytest.raises(ValueError):
        split_wh(1.0, [1], cap=0)
    with pytest.raises(ValueError):
        split_wh(1.0, [-1])


class FakeDcgm:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    def gpu_ids(self):
        return [0]

    def read(self, gpu):
        t = self.clock()
        return {
            "DCGM_FI_DEV_POWER_USAGE": 300.0,
            "DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION": int(300.0 * t * 1000),
            "DCGM_FI_DEV_SM_CLOCK": 1980,
            "DCGM_FI_DEV_MEM_CLOCK": 2619,
            "DCGM_FI_DEV_GPU_TEMP": 55,
            "DCGM_FI_DEV_GPU_UTIL": 90,
        }


class FakeZeus:
    gpu_indices = [0, 1]

    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    def power_w(self, gpu):
        return 250.0

    def energy_j(self, gpu):
        return 250.0 * self.clock()


@pytest.mark.parametrize(
    ("cls", "fake", "gpus", "watts"),
    [(DcgmMeter, FakeDcgm, 1, 300.0), (ZeusMeter, FakeZeus, 2, 250.0)],
)
def test_stub_backends_measure(tmp_path: Path, cls, fake, gpus, watts):
    clock = Clock()
    meter = cls(fake(clock), run_id=RUN_ID, traces_root=tmp_path, clock=clock, sleep=clock.sleep)
    meter.start("rep1", seconds=30)
    window = meter.stop()
    assert window.source == cls.source
    expected = gpus * watts * 30 / 3600
    assert window.wh == pytest.approx(expected, rel=0.01)
    assert window.method == "counter"
    wh, method = trace_watt_hours(window.csv_path)
    assert wh == pytest.approx(window.wh)
    digest = hashlib.sha256(Path(window.csv_path).read_bytes()).hexdigest()
    assert digest == window.sha256


def test_open_meter_dispatch():
    assert set(BACKENDS) == {"nvml", "dcgm", "zeus"}
    with pytest.raises(DcgmUnavailable):
        open_meter("dcgm", RUN_ID)
    with pytest.raises(ZeusUnavailable):
        open_meter("zeus", RUN_ID)
    with pytest.raises(ValueError):
        open_meter("bogus", RUN_ID)
