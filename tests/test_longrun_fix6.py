"""Fix 6: free-disk abort, live SAM/Wonder, and the end-of-run no-mock results audit."""

from __future__ import annotations

import json

from wattnot.longrun import nomock
from wattnot.longrun.driver import Driver, FakeVm, plan, plan_glimmer_long, plan_glimmer_max


def _driver(tmp_path, vm, **kw):
    return Driver(tmp_path, plan(tmp_path), vm, label="MOCK", run=lambda b: 0, max_hours=18.9,
                  balance_inr=lambda: 9000, hours_used=lambda: 0, sleep=lambda s: None,
                  log=lambda m: None, **kw)


def test_low_disk_aborts_cleanly_and_deletes_vm(tmp_path):
    vm = FakeVm()
    st = _driver(tmp_path, vm, disk_floor_gb=15.0, disk_free_gb=lambda: 3.0).go()
    assert st["stopped"].startswith("disk low") and vm.deleted
    assert any(e["kind"] == "disk-low" for e in st["events"])


def test_ample_disk_runs_to_finish(tmp_path):
    vm = FakeVm()
    st = _driver(tmp_path, vm, disk_floor_gb=15.0, disk_free_gb=lambda: 500.0).go()
    assert st["stopped"] == "finished"


def test_disk_check_off_when_floor_zero(tmp_path):
    vm = FakeVm()
    st = _driver(tmp_path, vm, disk_floor_gb=0.0, disk_free_gb=lambda: 0.1).go()
    assert st["stopped"] == "finished"   # floor 0 disables the check


def test_live_sweep_runs_sam_and_wonder_live(tmp_path):
    # the long/max plan's BF16 sweep must drive the REAL SAM and the REAL Wonder planner
    for blocks in (plan_glimmer_long(tmp_path), plan_glimmer_max(tmp_path)):
        sweep = next(b for b in blocks if b.id == "t1-sweep")
        argv = sweep.live
        assert "--wonder-live" in argv
        i = argv.index("--sam")
        assert argv[i + 1] == "live"


def test_audit_results_flags_mock(tmp_path):
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "spoken_answer.json").write_text(
        json.dumps({"label": "SYNTHETIC", "answers_label": "MOCK"}))
    rep = nomock.audit_results(tmp_path)
    assert not rep["ok"] and rep["findings"]
    assert "answers_label" in rep["findings"][0]["what"]


def test_audit_results_passes_measured(tmp_path):
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "tiers_summary.json").write_text(
        json.dumps({"label": "MEASURED", "answers_label": "MEASURED"}))
    rep = nomock.audit_results(tmp_path)
    assert rep["ok"] and rep["scanned"] >= 1


def test_audit_results_skips_own_reports(tmp_path):
    # the preflight report mentions MOCK in its text but must not flag itself
    nomock.write_report({"ok": True, "findings": [], "label_note": "MOCK sources banned"},
                        tmp_path / "nomock-preflight.json")
    rep = nomock.audit_results(tmp_path)
    assert rep["ok"]
