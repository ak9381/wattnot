
from wattnot.spark_open.hosted_ab import MockHost, TASKS, run_ab, OWN_GPU_ONLY



def test_mock_ab_runs_both_hosts_and_marks_own_gpu_levers(tmp_path):
    r = run_ab({"baseten": MockHost("baseten"), "modal": MockHost("modal")}, [], tasks=TASKS, state_dir=tmp_path)
    for h in ("baseten", "modal"):
        v = r["hosts"][h]
        assert v["status"] == "ran"
        for k in OWN_GPU_ONLY:
            assert v["levers"][k]["status"] == "own_gpu_only"
        assert v["levers"]["waste"]["calls_with"] < v["levers"]["waste"]["calls_without"]
        assert v["without"]["wh_per_1k"]["label"] == "ESTIMATE"
    assert r["spent_usd"] < 5


def test_host_down_is_not_run(tmp_path):
    r = run_ab({"baseten": MockHost("baseten"), "modal": MockHost("modal", down=True)}, [], tasks=TASKS, state_dir=tmp_path)
    assert r["hosts"]["modal"]["status"] == "not_run" and "error" in r["hosts"]["modal"]["why"]


def test_hard_stop(tmp_path):
    r = run_ab({"baseten": MockHost("baseten")}, [], tasks=TASKS * 50, cap_usd=0.0001, state_dir=tmp_path)
    assert r["hosts"]["baseten"]["status"] == "stopped"
    assert r["hosts"]["modal"]["status"] == "not_run"


def test_saved_estimate_formula():
    from wattnot.spark_open.hosted_ab import saved_estimate, J_PER_TOKEN, WATER_L_PER_KWH
    assert WATER_L_PER_KWH == 1.8
    assert abs(J_PER_TOKEN - 0.14966580932784637 * 52.5) < 1e-9
    e = saved_estimate(1_000_000, 600_000, 2.0, 1.2)
    j = 400_000 * J_PER_TOKEN
    assert e["energy_j"]["value"] == round(j, 3)
    assert abs(e["energy_kwh"]["value"] - j / 3.6e6) < 1e-12
    assert abs(e["water_l"]["value"] - j / 3.6e6 * WATER_L_PER_KWH) < 1e-12
    assert e["usd_saved"]["value"] == 0.8 and e["usd_saved"]["label"] == "MEASURED"
    assert e["water_l"]["label"] == e["energy_j"]["label"] == "ESTIMATE"
    assert e["power_saved_pct"]["value"] == 40.0
    assert saved_estimate(0, 0, 0, 0)["power_saved_pct"]["value"] is None
