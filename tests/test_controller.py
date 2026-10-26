from __future__ import annotations

import os
import signal
import threading
from types import SimpleNamespace

import pytest
from tests.fixtures.nvml_fake import DEFAULT_POWER_LIMIT_MW, FakeNVML

from wattnot.control import CapController, CapGuard, step_down


def _p99(cap_w: float) -> float:
    """Toy model: latency climbs as the cap drops below 400 W."""
    return 1.0 if cap_w >= 400 else 1.0 + (400 - cap_w) / 200


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _gate(sla=1.10):
    def gate(p99):
        ok = p99 <= sla
        return SimpleNamespace(passed=ok, failed=[] if ok else ["p99"])
    return gate


def test_guard_restores_on_exit_and_exception():
    nv = FakeNVML()
    with pytest.raises(RuntimeError), CapGuard(nv, 0, handle_sigterm=False) as g:
        g.set_cap_w(300)
        raise RuntimeError("x")
    assert nv.current_limit_mw == DEFAULT_POWER_LIMIT_MW


def test_guard_clamps_to_driver_range():
    nv = FakeNVML()
    with CapGuard(nv, 0, handle_sigterm=False) as g:
        assert g.set_cap_w(50) == 200
        assert g.set_cap_w(9999) == 700
    assert all(200_000 <= mw <= 700_000 for mw in nv.power_history)


def test_guard_restores_on_sigterm():
    nv = FakeNVML()
    with pytest.raises(SystemExit), CapGuard(nv, 0) as g:
        g.set_cap_w(300)
        os.kill(os.getpid(), signal.SIGTERM)
    assert nv.current_limit_mw == DEFAULT_POWER_LIMIT_MW


def test_step_down_stops_and_reverts_at_first_fail():
    nv = FakeNVML()
    with CapGuard(nv, 0, handle_sigterm=False) as g:
        best, steps = step_down(g, start_w=500, floor_w=200, step_w=50,
                                trial=_p99, gate=_gate())
        assert best == 400
        assert [s.cap_w for s in steps] == [450, 400, 350]
        assert steps[-1].passed is False and steps[-1].failed_gates == ("p99",)
        assert g.cap_w() == 400
    assert nv.current_limit_mw == DEFAULT_POWER_LIMIT_MW


def test_step_down_respects_floor():
    nv = FakeNVML()
    with CapGuard(nv, 0, handle_sigterm=False) as g:
        best, steps = step_down(g, start_w=500, floor_w=450, step_w=50,
                                trial=lambda c: 1.0, gate=_gate())
        assert best == 450 and len(steps) == 1


def test_live_controller_converges_in_range():
    nv, clock = FakeNVML(), Clock()
    with CapGuard(nv, 0, handle_sigterm=False) as g:
        c = CapController(g, p99_sla_s=1.5, min_w=250, max_w=700, clock=clock)
        for _ in range(200):
            c.tick(_p99(g.cap_w()))
            clock.t += 0.1
        caps = [cap for _, cap in c.changes[-6:]]
        assert all(250 <= cap <= 700 for _, cap in c.changes)
        assert caps[-1] == 350 and g.cap_w() == 350  # settled at the knee, no flapping
        assert _p99(g.cap_w()) <= 1.5
    assert nv.current_limit_mw == DEFAULT_POWER_LIMIT_MW


def test_hysteresis_limits_change_rate():
    nv, clock = FakeNVML(), Clock()
    with CapGuard(nv, 0, handle_sigterm=False) as g:
        c = CapController(g, p99_sla_s=1.5, min_w=250, max_w=700, clock=clock)
        for _ in range(300):
            c.tick(_p99(g.cap_w()))
            clock.t += 0.1
        for second in range(30):
            n = sum(1 for t, _ in c.changes if second <= t < second + 1)
            assert n <= 2


def test_loop_restores_on_stop_and_error():
    nv = FakeNVML()
    g = CapGuard(nv, 0, handle_sigterm=False)
    g.__enter__()
    c = CapController(g, p99_sla_s=2.0, min_w=250, max_w=700, hold_s=0)
    stop = threading.Event()
    n = {"i": 0}

    def read():
        n["i"] += 1
        if n["i"] > 3:
            raise RuntimeError("telemetry lost")
        return 0.5

    with pytest.raises(RuntimeError):
        c.loop(read, stop, sleep=lambda s: None)
    assert nv.current_limit_mw == DEFAULT_POWER_LIMIT_MW


def test_bad_bounds():
    with pytest.raises(ValueError):
        CapController(CapGuard(FakeNVML(), 0), p99_sla_s=1, min_w=200, max_w=700,
                      headroom=0.99, danger=0.9)
