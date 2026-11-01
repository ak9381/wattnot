"""The max plan (--plan max): sized to the balance, caps consistent, nothing mocked live."""
import json
import os
import sys
from pathlib import Path

from wattnot.longrun import driver, nomock

ROOT = Path(__file__).resolve().parents[1]


def _env():
    vals = {}
    for line in (ROOT / "scripts/e2e/max-run.env").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            k, v = line.split("=", 1)
            vals[k] = float(v)
    return vals


def test_max_plan_fits_booking_with_margin(tmp_path):
    blocks = driver.plan(tmp_path, "glimmer-test", "max")
    total_h = sum(b.hours for b in blocks)
    assert total_h + 8 / 60 <= driver.MAX_BOOKING_H
    ids = [b.id for b in blocks]
    for req in driver.MAX_REQUIRED_BLOCKS:
        assert req in ids
    # BF16-only blocks run before the FP8 swap
    for b in ("t1-sweep-r2", "t1-sweep-r4", "t1-tiers"):
        assert ids.index(b) < ids.index("t2-fp8")
    assert ids[-1] == "t3-copy"


def test_max_plan_cost_under_cap_and_floor():
    est = driver.MAX_BOOKING_H * driver.INR_PER_H
    assert est <= driver.MAX_BUDGET_CAP_INR
    assert driver.MAX_BUDGET_CAP_INR + driver.BALANCE_FLOOR_INR <= 8801.29 + 1


def test_caps_env_matches_driver():
    e = _env()
    assert e["RUN3_MAX_H"] == driver.MAX_BOOKING_H
    assert e["RUN3_BUDGET_CAP_INR"] == e["BUDGET_CAP_INR"] == driver.MAX_BUDGET_CAP_INR
    assert e["RUN3_OFFBOX_MAX_H"] == e["MAX_HOURS"] > driver.MAX_BOOKING_H


def test_max_plan_live_is_real():
    assert nomock.audit_plan("glimmer-test", plan_name="max")["ok"]


def test_max_plan_default_hours():
    assert driver._default_hours("glimmer-test", "max") == driver.MAX_BOOKING_H


def test_offbox_defaults_follow_env():
    src = (ROOT / "src/wattnot/longrun/watchdog/offbox.py").read_text()
    assert 'os.environ.get("RUN3_BUDGET_CAP_INR", "2000")' in src
    assert 'os.environ.get("RUN3_OFFBOX_MAX_H", "3.6")' in src


def test_public_bench_big_slices_pinned():
    sys.path.insert(0, str(ROOT / "scripts"))
    import public_bench_ab as pb
    assert len(pb.load("gsm8k300")) == 300 and len(pb.load("humaneval164")) == 164
    assert pb.load("gsm8k300")[:60] == pb.load("gsm8k")
    assert pb.grader("gsm8k300") is pb.GRADERS["gsm8k"]


def test_serverless_break_even(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import serverless_ab as sl
    assert sl.break_even_s(45000, 115, 70) == 1000.0
    assert sl.break_even_s(45000, 70, 70) is None
    assert sl.main(["--fake", "--out", str(tmp_path)]) == 0
    s = json.loads((tmp_path / "serverless_summary.json").read_text())
    assert s["label"] == "SIMULATED"
