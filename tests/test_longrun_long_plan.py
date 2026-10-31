"""The long-run plan (--plan long): plan only, nothing launches."""
import subprocess
import sys
from pathlib import Path

from wattnot.longrun import driver as d


def _plan(tmp_path):
    return {b.id: b for b in d.plan(tmp_path, model="glimmer-test", plan_name="long")}


def test_skipped_blocks_are_back(tmp_path):
    p = _plan(tmp_path)
    for bid in d.LONG_REQUIRED_BLOCKS:
        assert bid in p, bid


def test_wonder_is_live_with_bigger_budget(tmp_path):
    sweep = _plan(tmp_path)["t1-sweep"].live
    assert "--wonder-live" in sweep
    assert sweep[sweep.index("--sweep-budget-s") + 1] == str(d.LONG_WONDER_SWEEP_BUDGET_S)
    assert d.LONG_WONDER_SWEEP_BUDGET_S > 1500
    assert not any("mock" in str(a) for a in sweep)
    assert _plan(tmp_path)["t1-wonder"].hours * 60 >= d.LONG_WONDER_CHART_MIN - 1e-9


def test_live_commands_never_fake(tmp_path):
    for b in _plan(tmp_path).values():
        if b.live:
            assert "--fake" not in " ".join(map(str, b.live)), b.id


def test_fits_booking(tmp_path):
    total_h = sum(b.hours for b in _plan(tmp_path).values())
    assert total_h + 8 / 60 <= d.LONG_BOOKING_H


def test_public_block_dry_runs_all_gsm8k_items(tmp_path):
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, str(root / "scripts/public_bench_ab.py"), "--fake",
                        "--cap", "325", "--sets", "gsm8k", "--out", str(tmp_path)],
                       capture_output=True, text=True, check=True)
    assert f'"items": {d.LONG_GSM8K_ITEMS}' in r.stdout
