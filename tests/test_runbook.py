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
