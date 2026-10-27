"""T25 prep: the H100 runbook dry-runs end to end on the fake rig."""

import csv
import json

from wattnot import runbook as R
from wattnot.models import DEFAULT, LLAMA_31_8B, MUSE_GLIMMER, MUSE_SPARK_OPEN, RUN_ORDER, fits
from wattnot.verify import verify_receipt

QUICK = R.Plan(caps=(700, 600, 500), reps=5, repeat=1, concurrency=1, ablation_trials=4)
TAG = "20261030_090000"


def _run(tmp_path, **rig):
    out = tmp_path / "h100"
    code = R.run(R.FakeRig(**rig), out, plan=QUICK, key_path=tmp_path / "k.pem", run_tag=TAG)
    return code, out


def test_models_default_glimmer_then_llama_and_spark_open_does_not_fit():
    assert DEFAULT is MUSE_GLIMMER and RUN_ORDER == (MUSE_GLIMMER, MUSE_SPARK_OPEN, LLAMA_31_8B)
    assert fits(MUSE_GLIMMER, 80) and not fits(MUSE_SPARK_OPEN, 80)


def test_full_dry_run_writes_simulated_results_and_verifiable_receipts(tmp_path):
    code, out = _run(tmp_path, gpus=2)
    assert code == 0
    s = json.loads((out / "summary.json").read_text())
    assert all(v["ok"] for v in s["stages"].values()) and list(s["stages"]) == list(R.STAGES)
    assert s["models"][0] == MUSE_GLIMMER.repo
    rows = list(csv.DictReader((out / "sweep.csv").open()))
    assert {r["label"] for r in rows} == {"SIMULATED"}
    receipts = [p for p in (out / "receipts").glob("rcpt_*.json")
                if not p.name.endswith(".sigstore.json")]
    assert len(receipts) == 5  # headline x2, best x2, budget2
    for p in receipts:
        rep = verify_receipt(p)
        assert rep.grade == "SIMULATED"
        assert all(st.ok for st in rep.steps if st.name != "rekor")
    chain = [json.loads(p.read_text())["integrity"] for p in sorted(receipts)]
    assert sum(not c.get("prev_sha256") for c in chain) == 1  # one chain, one genesis
    assert json.loads((out / "ablation.json").read_text())["label"] == "SIMULATED"
    assert s["budget2"]["budget_w"]["freed_w"] > 0
    assert not list(out.rglob("*.pem"))  # signing key never lands in the copy-back folder
    assert (out / "MANIFEST.sha256").exists() and out.with_suffix(".tar.gz").exists()


def test_probe_failure_exits_20_and_stops(tmp_path):
    code, out = _run(tmp_path, cap_writable=False)
    assert code == R.EXIT_SETUP  # cap write is checked in setup already
    code, out = R.run(R.FakeRig(energy_counter=False), tmp_path / "p", plan=QUICK,
                      key_path=tmp_path / "k.pem", run_tag=TAG), tmp_path / "p"
    assert code == R.EXIT_PROBE
    s = json.loads((out / "summary.json").read_text())
    assert list(s["stages"]) == ["setup", "probe"] and not s["stages"]["probe"]["ok"]


def test_one_gpu_skips_budget2(tmp_path):
    code, out = _run(tmp_path)
    assert code == 0
    assert "skipped" in json.loads((out / "summary.json").read_text())["budget2"]


def test_cli_quick_dry_run(tmp_path):
    assert R.main(["--out", str(tmp_path / "h"), "--quick", "--stages", "setup,probe"]) == 0
    assert R.main(["--out", str(tmp_path / "f"), "--quick", "--fail-probe"]) == R.EXIT_SETUP


def test_spark_open_skipped_on_h100_and_run_on_8x_h200_sized_box(tmp_path):
    code, out = _run(tmp_path)
    s = json.loads((out / "summary.json").read_text())
    assert code == 0 and s["models"] == [MUSE_GLIMMER.repo, LLAMA_31_8B.repo]
    assert s["skipped_models"][0]["model"] == MUSE_SPARK_OPEN.repo
    rig = R.FakeRig(gpus=8)
    rig.vram_gb = 8 * 141.0  # 8x H200
    code = R.run(rig, tmp_path / "big", plan=QUICK, key_path=tmp_path / "k.pem", run_tag=TAG,
                 stages=("setup", "probe", "sweep"))
    s = json.loads((tmp_path / "big" / "summary.json").read_text())
    assert code == 0 and MUSE_SPARK_OPEN.repo in s["models"] and not s["skipped_models"]


# ---- the Oct 30 kit: probe, flex shed test, core sweep ------------------------------------


def _kit(tmp_path, **rig):
    out = tmp_path / "kit"
    code = R.run(R.FakeRig(**rig), out, plan=QUICK, stages=R.KIT_STAGES,
                 key_path=tmp_path / "k.pem", run_tag=TAG)
    return code, out


def test_flex_is_opt_in_not_in_the_default_stages():
    assert "flex" not in R.STAGES and "flex" in R.ORDER
    assert R.KIT_STAGES == ("setup", "probe", "flex", "sweep", "best")


def test_kit_dry_run_runs_in_order_and_everything_is_simulated(tmp_path):
    code, out = _kit(tmp_path)
    assert code == 0
    s = json.loads((out / "summary.json").read_text())
    assert list(s["stages"]) == list(R.KIT_STAGES) and all(v["ok"] for v in s["stages"].values())
    assert s["receipt_labels"] == {"SIMULATED": 5}  # 3 flex + 2 best-point
    assert [r["event"] for r in s["flex"]] == ["h100-evt-1", "h100-evt-2", "h100-evt-3"]
    assert all(r["label"] == r["grade"] == "SIMULATED" and r["trace_ok"] for r in s["flex"])
    assert s["flex"][2]["status"] == "partial"  # the 35 percent ask stops short and says so
    assert (out / "flex" / "progress.jsonl").stat().st_size > 0  # the load generator really ran


def test_kit_flex_receipts_are_chained_and_verify(tmp_path):
    _, out = _kit(tmp_path)
    s = json.loads((out / "summary.json").read_text())
    prev = None
    for row in s["flex"]:
        p = out / row["receipt"]
        rep = verify_receipt(p)
        assert rep.grade == "SIMULATED" and all(st.ok for st in rep.steps if st.name != "rekor")
        integ = json.loads(p.read_text())["integrity"]
        assert integ.get("prev_sha256") == prev
        prev = integ["sha256"]


def test_kit_failed_probe_stops_before_flex_and_sweep(tmp_path):
    code, out = _kit(tmp_path, energy_counter=False)
    assert code == R.EXIT_PROBE
    s = json.loads((out / "summary.json").read_text())
    assert list(s["stages"]) == ["setup", "probe"]
    assert not (out / "flex").exists() and not (out / "sweep.csv").exists()


def test_kit_script_fake_dry_run_exits_zero(tmp_path):
    import os
    import shutil
    import subprocess
    from pathlib import Path
    if not shutil.which("bash"):
        return
    root = Path(__file__).resolve().parents[1]
    out = tmp_path / "oct30"
    r = subprocess.run(["bash", str(root / "scripts" / "h100_oct30.sh"), "--fake",
                        "--out", str(out)], capture_output=True, text=True, timeout=300,
                       env={**os.environ, "HOME": str(tmp_path)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "receipt labels: {'SIMULATED': 5}" in r.stdout
    assert (tmp_path / "oct30.tar.gz").exists()


def test_kit_script_rejects_unknown_option():
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run(["bash", str(root / "scripts" / "h100_oct30.sh"), "--nope"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "unknown option" in r.stdout
