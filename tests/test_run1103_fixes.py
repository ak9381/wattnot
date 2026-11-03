import importlib.util
from pathlib import Path

from wattnot.longrun import driver


def _lc():
    p = Path(__file__).resolve().parents[1] / "scripts" / "langchain_pass.py"
    spec = importlib.util.spec_from_file_location("lc_pass_1103", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_calculator_takes_a_bare_number():
    calc = {t.name: t for t in _lc().tools()}["calculator"]
    assert calc.invoke({"expression": 30}) == "30"
    assert calc.invoke({"expression": "16-3-4"}) == "9"


class _FakeSsh:
    """A VM where the block finishes on the 2nd poll and the 1st poll's link drops."""
    def __init__(self):
        self.polls = 0

    def connect(self, host, key):
        return self

    def get_transport(self):
        return None

    def close(self):
        pass

    def exec_bounded(self, tr, cmd, timeout=60):
        if "echo started" in cmd:
            return 0, b"started\n"
        if "PENDING" in cmd:
            self.polls += 1
            if self.polls == 1:
                raise EOFError("link dropped")
            return 0, b"0\n"
        return 0, b"block output\n"


def test_detached_block_survives_a_dropped_poll(tmp_path):
    logf = tmp_path / "b.log"
    rc = driver.run_detached("p1-think", "true", 30, "h", "k", logf, poll_s=0, sshmod=_FakeSsh())
    assert rc == 0
    assert logf.read_bytes() == b"block output\n"


def test_detached_block_overrun_returns_124(tmp_path):
    class Never(_FakeSsh):
        def exec_bounded(self, tr, cmd, timeout=60):
            return (0, b"started\n") if "echo started" in cmd else (0, b"PENDING\n")
    assert driver.run_detached("x", "true", 0.05, "h", "k", tmp_path / "l", poll_s=0.01,
                               sshmod=Never()) == 124
