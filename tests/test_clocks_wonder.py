from pathlib import Path

from wattnot.proof.clocks import ClockLock
from wattnot.proof.fake import FakeClient, FakeNvml, proof_workload
from wattnot.proof.rig import Rig
from wattnot.proof.session import run_step
from wattnot.proof.wonder_planner import WonderPlanner, candidates, to_trial


class ClockNvml(FakeNvml):
    def __init__(self, refuse=False):
        super().__init__()
        self.refuse, self.locked, self.calls = refuse, None, []

    def set_locked_clocks(self, h, lo, hi):
        self.calls.append(("lock", lo))
        if self.refuse:
            raise RuntimeError("Insufficient Permissions")
        self.locked = (lo, hi)

    def reset_locked_clocks(self, h):
        self.calls.append(("reset",))
        self.locked = None


def _rig(nvml):
    wl = proof_workload()
    return Rig(nvml, nvml.device_handle(0), FakeClient(nvml), wl, repeat=1, settle_s=0,
               warmup=0, sleep=lambda s: None)


def test_lock_applies_and_resets():
    n = ClockNvml()
    lk = ClockLock(n, None)
    assert lk.apply("locked-mid") and n.locked == (1590, 1590)
    lk.reset()
    assert n.locked is None


def test_refusal_falls_back_to_caps_only_for_good():
    n = ClockNvml(refuse=True)
    lk = ClockLock(n, None)
    assert lk.apply("locked-high") is False and lk.available is False
    assert lk.apply("locked-mid") is False
    assert len([c for c in n.calls if c[0] == "lock"]) == 1  # never retried
    assert "Insufficient" in lk.reason


def test_wrapper_without_clock_api_is_caps_only():
    lk = ClockLock(FakeNvml(), None)
    assert lk.apply("locked-high") is False and lk.apply("base") is True


def test_step_locks_cap_arm_only(tmp_path: Path):
    n = ClockNvml()
    s = run_step(_rig(n), 350, 2, tmp_path, workload=proof_workload(), clock="locked-mid")
    assert s["clock_applied"] and s["clock_profile"] == "locked-mid"
    locks = [c for c in n.calls if c[0] == "lock"]
    assert len(locks) == 2 and n.locked is None  # cap arms locked, reset after


def test_step_records_fallback(tmp_path: Path):
    s = run_step(_rig(ClockNvml(refuse=True)), 350, 2, tmp_path, workload=proof_workload(),
                 clock="locked-mid")
    assert s["clock_applied"] is False and s["clock_profile"] == "base"
    assert s["clock_fallback"].startswith("caps-only")


def test_candidates_range():
    c = candidates()
    assert {w for w, _ in c} == {375, 350, 325, 300}
    assert ("350", "base") not in c and (350, "base") in c


def _row(cap, e, ok=True, clock="base", **kw):
    return {"cap_w": cap, "clock_profile": clock, "accepted": ok, "why_not": [],
            "energy_change_pct": e, "p99_change_pct": 5, "quality_pass_pct": 93.7,
            "stock_pass_pct": 93.7, **kw}


def test_planner_proposes_then_drops_clocks_after_fallback():
    p = WonderPlanner(mock=True)
    first = p.propose([])
    assert first["kind"] == "try" and first["cap_w"] == 375 and first["trace_id"]
    hist = [_row(375, -24, clock="base"), _row(375, -24, clock="base",
                                                 clock_fallback="caps-only: refused")]
    p.propose(hist)
    assert all(c[1] == "base" for c in p.sup.candidates)
    assert p.clock_fallback


def test_planner_stops_when_front_flattens():
    p = WonderPlanner(mock=True)
    hist = [_row(375, -27), _row(350, -27.0), _row(325, -27.0), _row(300, -27.0)]
    d = p.propose(hist)
    assert d["kind"] == "stop" and d["stop_flattened"]


def test_rejected_trials_are_off_the_front():
    assert to_trial(_row(325, -30, ok=False))["quality"] == 0.0


def test_supervisor_error_stops_cleanly():
    class Boom:
        model, mock, trace_ids, candidates = "m", False, [], candidates()

        def next_trial(self, h):
            raise TimeoutError("x")
    p = WonderPlanner(Boom())
    assert p.propose([])["kind"] == "stop"
