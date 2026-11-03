import importlib.util
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

_p = Path(__file__).resolve().parents[1] / "scripts" / "e2e" / "stop_after_block.py"
_spec = importlib.util.spec_from_file_location("stop_after_block", _p)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)
B, P = "p2-agentwaste", ("p1r",)


def st(**blocks):
    return {"blocks": {k: v for k, v in blocks.items()}}


def test_waits_while_the_block_runs_and_through_a_first_failed_attempt():
    assert m.should_stop(st(), {"block": "p2-langchain"}, B, P) is None
    assert m.should_stop(st(**{B: {"status": "running"}}), {"block": B}, B, P) is None
    # attempt 1 failed, the driver is about to retry the same block: keep waiting
    assert m.should_stop(st(**{B: {"status": "failed", "attempt": 1}}), {"block": B}, B, P) is None


def test_stops_once_the_block_is_really_over():
    for s in ("done", "skipped", "dropped", "lost"):
        assert m.should_stop(st(**{B: {"status": s}}), {"block": B}, B, P)
    assert m.should_stop(st(**{B: {"status": "failed", "attempt": 2}}), {"block": B}, B, P)


def test_safety_net_stops_on_any_rerun_block():
    assert "p1r-sweep" in m.should_stop(st(), {"block": "p1r-sweep"}, B, P)


def test_end_to_end_sends_one_sigint_and_never_relaunches(tmp_path):
    drv = subprocess.Popen([sys.executable, "-c",
                            "import signal,time,sys\n"
                            "signal.signal(signal.SIGINT, lambda *a: sys.exit(3))\n"
                            "time.sleep(60)"])
    (tmp_path / "heartbeat.json").write_text(json.dumps({"block": B}))
    (tmp_path / "driver_state.json").write_text(json.dumps(st(**{B: {"status": "running"}})))
    w = subprocess.Popen([sys.executable, str(_p), "--out", str(tmp_path), "--pid", str(drv.pid),
                          "--block", B, "--stop-prefix", "p1r", "--interval", "0.1", "--grace", "10"],
                         stdout=subprocess.PIPE, text=True)
    time.sleep(0.5)
    assert drv.poll() is None     # still running: block not over
    (tmp_path / "driver_state.json").write_text(json.dumps(st(**{B: {"status": "done"}})))
    out, _ = w.communicate(timeout=15)
    assert drv.wait(timeout=5) == 3
    assert w.returncode == 0 and "SIGINT" in out and "nothing relaunched" in out
    assert (tmp_path / "driver_state.after-pass2-stop.json").exists()
