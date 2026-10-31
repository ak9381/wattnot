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
from wattnot.spark_open.knobs import DEFAULT_QUANT, QUANTS, SparkKnobs, baseline, grid, pin_plan
from wattnot.spark_open.llama_server import LlamaServer, server_args
from wattnot.spark_open.placement import ExpertPlacement, LayerPlacement
from wattnot.spark_open.planner import max_hot_layers, plan, vram_estimate_gb
from wattnot.spark_open.stage import FakeSparkRig, cpu_share, main, preflight, run_stage


def test_defaults_and_flags():
    k = SparkKnobs()
    assert k.quant == DEFAULT_QUANT == "UD-IQ2_S" and k.flags == []
    assert "agentic floor" in SparkKnobs("UD-IQ1_S").flags[0]
    with pytest.raises(ValueError):
        SparkKnobs("Q8_0")
    with pytest.raises(ValueError):
        SparkKnobs(n_cpu_moe=63)
    assert len(grid()) == 32


def test_server_args_all_cpu_and_budget():
    a = server_args("/m/x.gguf", SparkKnobs())
    assert a[0] == "llama-server" and "--n-gpu-layers" in a and "99" in a
    assert "--cpu-moe" in a and "--n-cpu-moe" not in a and "--no-op-offload" not in a
    b = server_args("/m/x.gguf", SparkKnobs(n_cpu_moe=20, ubatch=4096, op_offload=False,
                                            threads=32))
    assert b[b.index("--n-cpu-moe") + 1] == "20" and "--cpu-moe" not in b
    assert b[b.index("--ubatch-size") + 1] == "4096" and b[b.index("--batch-size") + 1] == "4096"
    assert b[b.index("--threads") + 1] == "32" and "--no-op-offload" in b
    c = server_args("/m/x.gguf", SparkKnobs(n_cpu_moe=0))
    assert "--n-cpu-moe" not in c and "--cpu-moe" not in c


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
    assert not preflight(tmp_path, disk_free_gb=450, ram_gb=250).ok  # 260 + 220 GB
    assert "RAM" in preflight(tmp_path, disk_free_gb=900, ram_gb=64).reason
    assert preflight(tmp_path, disk_free_gb=900, ram_gb=250).ok


def test_fake_stage_chain_and_labels(tmp_path):
    s = run_stage(FakeSparkRig(), tmp_path)
    tries = s["tries"]
    assert len(tries) == 32 and tries[0]["is_baseline"] and not tries[0]["billable"]
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
    assert "32 tries" in capsys.readouterr().out
    assert main(["--rig", "fake", "--out", str(tmp_path), "--budget-h", "3"]) == 0
    s = json.loads((tmp_path / "spark_open" / "spark_open.json").read_text())
    assert len(s["tries"]) < 32 and s["plan"]["dropped"] and s["plan"]["est_hours"] <= 3


def test_tiers_are_per_layer_and_honest():
    t = SparkKnobs(n_cpu_moe=40, op_offload=True).tiers()
    assert t["granularity"] == "layer" and t["hot_layers_vram"] == 22
    assert t["warm_layers_ram_gpu_compute"] == t["cold_layers_cpu_compute"] == 40
    assert "not single experts" in t["note"]
    off = SparkKnobs(op_offload=False).tiers()
    assert off["warm_layers_ram_gpu_compute"] == 0 and "no-op-offload" in off["warm_rule"]
    assert "CPU comparison" in " ".join(SparkKnobs(op_offload=False).flags)
    with pytest.raises(ValueError):
        SparkKnobs(ubatch=8)


def test_planner_grid_fits_vram_and_budget():
    p = plan()
    assert len(p.tries) == 32 and p.tries[0] == baseline() and not p.dropped
    assert p.est_minutes <= 4.5 * 60 and p.label == "ESTIMATE"
    assert {k.quant for k in p.tries} == set(QUANTS)
    assert all(vram_estimate_gb(k) <= 80 for k in p.tries)
    for q in QUANTS:  # each quant's tries stay together: one download, one cold load
        idx = [i for i, k in enumerate(p.tries) if k.quant == q]
        assert idx == list(range(idx[0], idx[-1] + 1))
    assert max_hot_layers("UD-IQ1_S") > max_hot_layers("UD-IQ2_S")
    small = plan(budget_h=2.5)
    assert small.est_minutes <= 150 and small.dropped and small.tries[0] == baseline()


def test_receipt_has_tiers_and_cpu_share(tmp_path):
    s = run_stage(FakeSparkRig(), tmp_path)
    for t in s["tries"]:
        e = t["energy"]
        want = 100 * e["host_j"] / (e["gpu_j"] + e["host_j"])
        assert e["cpu_share_pct"] == pytest.approx(want, abs=0.01)
        assert t["tiers"]["granularity"] == "layer" and t["tiers"]["vram_label"] == "ESTIMATE"
    assert cpu_share(10, None) is None
    assert "per layer" in s["placement"]
    g = run_stage(FakeSparkRig(host_power=False), tmp_path / "g")
    assert all(t["energy"]["cpu_share_pct"] is None for t in g["tries"])


def test_expert_placement_emits_pin_flags(tmp_path):
    assert LayerPlacement().args(SparkKnobs()) == ["--cpu-moe"]
    mp = tmp_path / "pin.json"
    ep = ExpertPlacement({(0, 1): 3}, 1, map_path=mp)
    assert ep.granularity == "expert" and ep.plan["pinned"] == [[0, 1]]
    # args now serialize the hot/cold map and hand it to the (patched) vLLM via env.
    args = ep.args(SparkKnobs())
    assert args == ["--env", f"WATTNOT_EXPERT_PIN_MAP={mp}"] and mp.is_file()
    # With no counts and no pinned_experts there is nothing to pin: no flags.
    assert ExpertPlacement({}, 1, map_path=tmp_path / "none.json").args(SparkKnobs()) == []
