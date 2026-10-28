
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
