"""Spark Open side stage: llama-server args, knobs, RAPL, gates, fake stage."""

import json

import pytest

from wattnot.receipt.canon import canonical_sha256
from wattnot.spark_open.gate import (
    Energy,
    HostedReference,
    agreement_pct,
    energy_verdict,
    quality_verdict,
)
from wattnot.spark_open.hostpower import GPU_ONLY, Rapl
from wattnot.spark_open.knobs import DEFAULT_QUANT, SparkKnobs, grid, pin_plan
from wattnot.spark_open.llama_server import LlamaServer, server_args
from wattnot.spark_open.stage import FakeSparkRig, main, preflight, run_stage


def test_defaults_and_flags():
    k = SparkKnobs()
    assert k.quant == DEFAULT_QUANT == "UD-IQ2_S" and k.flags == []
    assert "agentic floor" in SparkKnobs("UD-IQ1_S").flags[0]
    with pytest.raises(ValueError):
        SparkKnobs("Q8_0")
    with pytest.raises(ValueError):
        SparkKnobs(n_cpu_moe=63)
    assert len(grid()) == 6


def test_server_args_all_cpu_and_budget():
    a = server_args("/m/x.gguf", SparkKnobs())
    assert a[0] == "llama-server" and "--n-gpu-layers" in a and "99" in a
    assert a[a.index("--override-tensor") + 1] == "exps=CPU" and "--n-cpu-moe" not in a
    b = server_args("/m/x.gguf", SparkKnobs(n_cpu_moe=20))
    assert b[b.index("--n-cpu-moe") + 1] == "20" and "--override-tensor" not in b


def test_dry_run_server_restarts_without_process(tmp_path):
    s = LlamaServer(tmp_path, dry_run=True, popen=lambda *a, **k: pytest.fail("spawned"))
    assert s.start(SparkKnobs()) == 0.0
    s.restart(SparkKnobs("UD-IQ1_S", 40))
    assert s.restarts == 2 and "UD-IQ1_S" in " ".join(s.argv)


def test_pin_plan_stub():
    assert pin_plan(None, 8)["mode"] == "layer"
    p = pin_plan({(0, 1): 5, (0, 2): 9, (3, 7): 1}, 2)
    assert p["mode"] == "expert" and p["pinned"] == [[0, 2], [0, 1]]


def test_rapl_reads_and_wraps(tmp_path):
    z = tmp_path / "intel-rapl:0"
    z.mkdir()
    (z / "energy_uj").write_text("900")
    (z / "max_energy_range_uj").write_text("1000")
    r = Rapl(tmp_path)
    assert r.available
    assert r.joules_between({"intel-rapl:0": 900}, {"intel-rapl:0": 100}) == pytest.approx(2e-4)
    assert not Rapl(tmp_path / "none").available


def test_gpu_only_never_counted():
    v = energy_verdict(Energy(100, None, 10), Energy(50, None, 10))
    assert v["boundary"] == GPU_ONLY and v["label"] == "ESTIMATE" and not v["counted"]
    assert v["gpu_saving_pct"] == 50


def test_shift_not_saved():
    v = energy_verdict(Energy(100, 100, 10), Energy(60, 135, 10))
    assert v["shifted_to_cpu"] and v["flag"] == "shifted, not saved" and not v["counted"]


def test_real_saving_counted():
    v = energy_verdict(Energy(100, 100, 10), Energy(90, 60, 10))
    assert v["counted"] and v["total_saving_pct"] == 25 and v["label"] == "MEASURED"


def test_quality_gate():
    ref = {"a": "x", "b": "y"}
    assert agreement_pct(ref, {"a": " X ", "b": "y"}) == 100
    q = quality_verdict(ref, [1, 1], {"a": "x", "b": "n"}, [1, 1])
    assert not q["passed"] and q["drop_pts"] == 50
    assert not quality_verdict(ref, [1, 1], ref, [2, 2])["p99_ok"]


def test_hosted_reference_off_by_default():
    assert not HostedReference.from_env({}).enabled
    r = HostedReference.from_env({"WATTNOT_SPARK_REF_URL": "https://x",
                                  "WATTNOT_SPARK_REF_ENABLE": "1",
                                  "WATTNOT_SPARK_REF_KEY_ENV": "BASETEN_KEY"})
    assert r.enabled and "BASETEN_KEY" not in json.dumps(r.describe())


def test_preflight(tmp_path):
    assert not preflight(tmp_path, disk_free_gb=100, ram_gb=250).ok
    assert "RAM" in preflight(tmp_path, disk_free_gb=900, ram_gb=64).reason
    assert preflight(tmp_path, disk_free_gb=900, ram_gb=250).ok


def test_fake_stage_chain_and_labels(tmp_path):
    s = run_stage(FakeSparkRig(), tmp_path)
    tries = s["tries"]
    assert len(tries) == 6 and tries[0]["is_baseline"] and not tries[0]["billable"]
    prev = None
    for t in tries:
        assert t["prev_sha256"] == prev
        body = {k: v for k, v in t.items() if k != "sha256"}
        assert canonical_sha256(body) == t["sha256"]
        prev = t["sha256"]
        assert t["energy"]["gpu_label"] == t["energy"]["host_label"] == "SIMULATED"
    shifted = [t for t in tries if t["savings"]["flag"] == "shifted, not saved"]
    assert shifted and not any(t["billable"] for t in shifted)
    assert all(t["knobs"]["flags"] for t in tries if t["knobs"]["quant"] == "UD-IQ1_S")
    assert s["best"] and s["best"]["label"] == "SIMULATED"


def test_fake_stage_gpu_only(tmp_path):
    s = run_stage(FakeSparkRig(host_power=False), tmp_path)
    assert all(t["boundary"] == GPU_ONLY and not t["billable"] for t in s["tries"])
    assert s["best"] is None


def test_cli_fake(tmp_path, capsys):
    assert main(["--rig", "fake", "--out", str(tmp_path), "--quick"]) == 0
    assert (tmp_path / "spark_open" / "spark_open.json").is_file()
    assert "6 tries" in capsys.readouterr().out
