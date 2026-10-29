import importlib.util, json, threading
from pathlib import Path
import pytest
p = Path(__file__).resolve().parents[1] / "modal/spark_topk_sprint.py"
spec = importlib.util.spec_from_file_location("sprint", p); S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

def test_plan_under_budget():
    pl = S.plan(); assert pl["minutes"] == 90 and pl["usd_estimate"] <= 80

def test_stock_has_no_override():
    assert "--hf-overrides" not in S.serve_args(8)
    a = S.serve_args(4); assert json.loads(a[a.index("--hf-overrides") + 1]) == {"num_experts_per_tok": 4}

def test_resume_skips_done():
    assert S.done_arms({"8": {"status": "done"}, "6": {"status": "partial"}}) == [8]


def test_stage_timeout_refuses_launch_at_no_headroom(monkeypatch):
    # When the $98 guard has no headroom cloud_timeout_s returns 0, so the stage must not launch.
    import wattnot.spend.guard as guard
    monkeypatch.setattr(guard, "cloud_timeout_s", lambda g: 0)
    with pytest.raises(SystemExit, match="no headroom"):
        S.stage_timeout(300, S.T4_USD_H, local=True)


def test_stage_timeout_remote_returns_cap_unchanged():
    # On the container (local=False) wattnot is not importable; the cap passes through.
    assert S.stage_timeout(1234, S.T4_USD_H, local=False) == 1234


def test_kill_timer_fires_stop_then_exit():
    fired = {}
    done = threading.Event()

    def stop():
        fired["stop"] = True

    def exit_(code):
        fired["exit"] = code
        done.set()

    t = S.kill_timer(0, "wattnot-spark-topk-draft", grace_s=0, stop=stop, exit_=exit_)
    assert done.wait(2.0), "kill timer never fired"
    t.join(1.0)
    assert fired == {"stop": True, "exit": 3}
