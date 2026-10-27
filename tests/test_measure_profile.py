"""Measurement profiles and the noise controls: the knobs exist, are configurable, and do what
they say (on the fake GPU; nothing here measures real energy)."""

from __future__ import annotations

import time

import pytest
from tests.test_proof import WL, FakeClient, FakeNvml

from wattnot.proof import profile as P
from wattnot.proof.rig import Rig
from wattnot.proof.session import burn_in, run_control, run_step, stock_noise


def rig(**kw):
    nvml = FakeNvml()
    base = {"concurrency": 8, "repeat": 1, "settle_s": 0, "warmup": 2, "sleep": lambda s: None}
    base.update(kw)
    r = Rig(nvml, object(), FakeClient(nvml), WL, **base)
    r.order: list[tuple[str, int]] = []
    real = r.block

    def spy(cap_w, **k):
        r.order.append(("stock" if cap_w == r.default_w else "cap", cap_w))
        return real(cap_w, **k)

    r.block = spy
    return r


def test_profiles_are_ordered_and_quick_is_run2():
    q, s, lg = (P.PROFILES[n] for n in ("quick", "standard", "long"))
    assert (q.reps, q.repeat) == (3, 2) and q.burn_in == 0 and not q.counterbalance  # what RUN2 ran
    assert q.reps < s.reps < lg.reps and q.repeat < s.repeat < lg.repeat
    assert s.step_seconds() > q.step_seconds()  # ESTIMATE: longer blocks cost wall time
    assert P.DEFAULT == "standard"


def test_resolve_lets_any_flag_override_the_profile():
    p = P.resolve("long", reps=9, warmup_s=0.0, counterbalance=False)
    assert (p.reps, p.warmup_s, p.counterbalance) == (9, 0.0, False)
    assert p.repeat == P.PROFILES["long"].repeat  # not given: the profile's own value
    assert P.resolve(None).as_dict() == P.PROFILES["standard"].as_dict()
    with pytest.raises(KeyError):
        P.resolve("nope")


def test_cli_flags_reach_the_profile():
    import argparse

    ap = argparse.ArgumentParser()
    P.add_args(ap)
    a = ap.parse_args(["--profile", "quick", "--reps", "4", "--counterbalance"])
    p = P.resolve(a.profile, **{k: getattr(a, k) for k in P.FIELDS})
    assert (p.reps, p.repeat, p.counterbalance) == (4, 2, True)


def test_counterbalance_alternates_which_arm_goes_first(tmp_path):
    r = rig(counterbalance=True)
    run_step(r, 500, 4, tmp_path, workload=WL)
    firsts = [r.order[i][0] for i in range(0, 8, 2)]
    assert firsts == ["stock", "cap", "stock", "cap"]
    r2 = rig(counterbalance=False)
    run_step(r2, 500, 4, tmp_path, workload=WL)
    assert [r2.order[i][0] for i in range(0, 8, 2)] == ["stock"] * 4
    assert rig().default_w == 700  # the fake card's stock cap


def test_pairs_stay_matched_when_counterbalanced(tmp_path):
    s = run_step(rig(counterbalance=True), 500, 4, tmp_path, workload=WL)
    assert len(s["j_per_req_stock"]) == len(s["j_per_req_cap"]) == 4
    assert len(s["blocks"]["stock"]) == len(s["blocks"]["cap"]) == 4
    assert s["accepted"], s["why_not"]


def test_burn_in_is_one_unmetered_stock_block_per_process(tmp_path):
    r = rig(burn_in=1)
    run_step(r, 500, 2, tmp_path, workload=WL)
    assert len(r.order) == 1 + 2 * 2 and r.order[0][0] == "stock"
    n = len(r.order)
    burn_in(r)  # once per process: a second call is a no-op
    assert len(r.order) == n
    s = run_step(rig(burn_in=0), 500, 2, tmp_path, workload=WL)
    assert len(s["blocks"]["stock"]) == 2  # the burn-in block is never one of the measurements


def test_control_runs_extra_stock_blocks_and_reports_noise(tmp_path):
    c = run_control(rig(control_blocks=3), tmp_path, workload=WL)
    assert c["stock_blocks"] == 5 and len(c["stock_j_per_req"]) == 5
    assert c["stock_noise_cv_pct"] >= 0 and c["stock_range_pct"] >= 0
    assert len(c["blocks"]["extra_stock"]) == 3
    assert c["bad_setting_rejected"] and c["stock_vs_stock_accepted"]
    c0 = run_control(rig(control_blocks=0), tmp_path, workload=WL)
    assert c0["stock_blocks"] == 2 and "extra_stock" not in c0["blocks"]


def test_step_reports_stock_noise(tmp_path):
    s = run_step(rig(), 500, 3, tmp_path, workload=WL)
    assert s["stock_noise"]["label"] == "MEASURED" and s["stock_noise"]["blocks"] == 3
    assert stock_noise([{"j_per_req": 10.0}, {"j_per_req": 10.0}])["cv_pct"] == 0


def test_timed_warmup_keeps_the_card_busy_first(tmp_path):
    r = rig(warmup_s=0.3)
    t0 = time.perf_counter()
    r.block(r.default_w)
    assert time.perf_counter() - t0 >= 0.3
