"""The max plan (--plan max): sized to the balance, caps consistent, nothing mocked live."""
import json
import os
import sys
from pathlib import Path

import pytest

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
    for req in driver.MAX_REQUIRED_BLOCKS + driver.MAX_EXTRA_BLOCKS:
        assert req in ids
    by = {b.id: b for b in blocks}
    assert all(by[x].skip_if_short for x in driver.MAX_EXTRA_BLOCKS)
    # BF16-only blocks (every pass) run before the FP8 swap
    for b in ("p2-sweep", "p1r-agentwaste", "p1-langchain", "t2g-serverless"):
        assert ids.index(b) < ids.index("t2-fp8")
    assert ids[-1] == "t3-copy"
    # off-GPU work is not on the VM
    assert "t1-wonder" not in ids and "t1-eu" not in ids


def test_each_pass_has_every_gpu_layer_and_own_dataset(tmp_path):
    ids = [b.id for b in driver.plan(tmp_path, "glimmer-test", "max")]
    seen = set()
    for pas, sweep, grid, ds in driver.MAX_PASSES:
        assert sweep in ids
        for layer in driver.MAX_PASS_LAYERS_MIN:
            if layer != "sweep":
                assert f"{pas}-{layer}" in ids
        seen.add(ds)
    assert seen == {"own", "gsm8k300"}


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


def test_flow_runs_off_gpu_and_signs(tmp_path):
    from wattnot import flow
    from wattnot.receipt.sign import bundle_path, verify_offline_bundle
    assert flow.main(["--out", str(tmp_path / "f"), "--dataset", "own"]) == 0
    fr = tmp_path / "f" / "flow-receipt.json"
    r = json.loads(fr.read_text())
    ok, _ = verify_offline_bundle(r, json.loads(bundle_path(fr).read_text()))
    assert ok and r["gpu"] == "replay"
    labels = {x["id"]: x["label"] for x in r["layers"]}
    assert labels["gpu-sweep"] == "REPLAY" and labels["langchain"] == "MOCK"
    assert all(x["status"] == "ok" for x in r["layers"])


def test_flow_reads_a_gpu_pass_folder(tmp_path):
    import shutil
    from wattnot import flow
    g = tmp_path / "pass"
    (g / "sweep").mkdir(parents=True)
    for p in (ROOT / "receipts/rerun-1031").iterdir():
        shutil.copy2(p, g / "sweep" / p.name)
    (g / "langchain").mkdir()
    (g / "langchain" / "langchain_pass.json").write_text("{}")
    assert flow.main(["--out", str(tmp_path / "f"), "--gpu-dir", str(g)]) == 0
    r = json.loads((tmp_path / "f" / "flow-receipt.json").read_text())
    modes = {x["id"]: (x["mode"], x["label"]) for x in r["layers"]}
    assert modes["gpu-sweep"] == ("gpu-live", "MEASURED")
    assert modes["langchain"] == ("gpu-live", "MEASURED")
    assert modes["eye-gate"][0] == "off-gpu-fake"   # missing in the pass: faked, not passed off


def test_langchain_pass_mock_and_multiprovider_plan(tmp_path):
    import subprocess
    r = subprocess.run([sys.executable, "scripts/langchain_pass.py", "--fake", "--n", "2",
                        "--dataset", "humaneval164", "--out", str(tmp_path / "lc")], cwd=ROOT)
    assert r.returncode == 0
    lc = json.loads((tmp_path / "lc" / "langchain_pass.json").read_text())
    assert lc["label"] == "MOCK" and set(lc["arms"]) == {"saver_off", "saver_on"}
    sys.path.insert(0, str(ROOT / "scripts"))
    import multiprovider_route as mp
    plan = mp.plan(20, 2)
    assert plan["needs_go"] and plan["label"] == "ESTIMATE"
    d = mp.decide({"a": {"own": {"n": 10, "correct": 10, "j": 100, "usd": 1}},
                   "b": {"own": {"n": 10, "correct": 5, "j": 10, "usd": 0.1}}})
    assert d["own"]["route_to"] == "a"   # b is cheaper but fails the quality gate
    # leg 4 carries the spend caps: $8/round Baseten hard stop, Meta $100 soft / $120 hard,
    # and the paid key env (never the dead free key)
    c = plan["caps"]
    assert c["baseten_round_hard_usd"] == 8.0
    assert c["meta_soft_usd"] == 100.0 and c["meta_hard_usd"] == 120.0
    assert c["baseten_key_env"] == "BASETEN_METRICS_KEY"
    assert "--cap-usd 8" in " ".join(plan["live_commands_not_run"])


def test_multiprovider_baseten_round_cap_hard_stops():
    sys.path.insert(0, str(ROOT / "scripts"))
    import multiprovider_route as mp
    over = {"baseten": {"own": {"n": 100, "correct": 100, "j": None, "usd": 9.0}}}
    with pytest.raises(mp.CapExceeded):
        mp.enforce_caps(over)
    under = {"baseten": {"own": {"n": 100, "correct": 100, "j": None, "usd": 3.0}}}
    assert mp.enforce_caps(under)["within_cap"]


def test_phase_clock_creates_missing_out_dir(tmp_path):
    from wattnot.control import phase_clock
    d = tmp_path / "p1-phase"
    rc = phase_clock.main(["--mock", "--duration-s", "0.3", "--summary", str(d / "summary.json"),
                           "--log", str(d / "log.jsonl")])
    assert not rc
    assert (d / "log.jsonl").exists()


def test_matrix_legs_3_4_inside_booking(tmp_path):
    blocks = driver.plan(tmp_path, "glimmer-test", "max")
    by = {b.id: b for b in blocks}
    for bid, _t, rounds in driver.MAX_MATRIX_LEGS:
        b = by[bid]
        assert b.host and b.skip_if_short and b.trim and b.trim_live
        live = " ".join(b.live)
        for r in rounds:
            assert f"WATTNOT_MATRIX_ROUND={r} WATTNOT_MATRIX_RECORD=1" in live
        assert "--fake" not in live and "MOCK" not in live
    assert [r for _b, _t, rs in driver.MAX_MATRIX_LEGS for r in rs] == \
        ["r07", "r08", "r09", "r10", "r11", "r12"]
    total_h = sum(b.hours for b in blocks)
    assert total_h + 8 / 60 <= driver.MAX_BOOKING_H
    assert total_h * driver.INR_PER_H <= driver.MAX_BUDGET_CAP_INR


def test_model_glimmer_maps_to_the_h100_setting(tmp_path, monkeypatch):
    seen = {}

    def fake_dry(out, **kw):
        seen.update(kw)
        return {"blocks": {}, "stopped": "finished", "sim": {}}
    monkeypatch.setattr(driver, "dry_run", fake_dry)
    try:
        driver.main(["--dry-run", "--plan", "max", "--model", "glimmer", "--out", str(tmp_path)])
    except SystemExit as e:
        assert e.code in (0, None)
    assert seen.get("model") == "glimmer-test" and seen.get("plan_name") == "max"


def test_p1r_rerun_after_pass2_reads_its_own_sweep(tmp_path):
    blocks = driver.plan(tmp_path, "glimmer-test", "max")
    ids = [b.id for b in blocks]
    by = {b.id: b for b in blocks}
    assert not any(i.startswith("p3-") for i in ids)
    assert ids.index("p2-agentwaste") < ids.index("p1r-sweep") < ids.index("p1r-agentwaste")
    assert by["p1r-tiers"].sweep_tag == "p1r-sweep" and "p1r-sweep" in by["p1r-tiers"].depends_on
    assert by["p2-tiers"].sweep_tag == "t1-sweep" and by["p1-tiers"].sweep_tag == "t1-sweep"
    assert any("runs/p1r-sweep" in a for a in by["p1r-sweep"].live)
    assert any("/p1r-tiers" in a for a in by["p1r-tiers"].live)
    import json
    for tag, cap in (("t1-sweep", 350), ("p1r-sweep", 325)):
        f = tmp_path / "runs" / tag / "vm" / "results.json"
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps({"best_cap_w": cap}))
    assert driver.best_cap_from_sweep(tmp_path, "p1r-sweep") == 325
    assert driver.best_cap_from_sweep(tmp_path) == 350
