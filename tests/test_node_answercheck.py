"""GPU helper on a 'real' GPU (fake NVML): the live answer check keeps or rolls back caps, a
failure always restores stock, and the rack check works without the mask decoder."""
import pytest

from wattnot.node import agent
from wattnot.node.answercheck import AnswerCheck


class FakeGpu:
    label = "MEASURED"
    def __init__(self):
        self.cap, self.e = 700, 0
        self.set_calls = []
    def nvmlDeviceGetName(self, h): return "H100"
    def nvmlDeviceGetCount(self): return 1
    def nvmlDeviceGetPowerUsage(self, h): return 300_000
    def nvmlDeviceGetPowerManagementLimit(self, h): return self.cap * 1000
    def nvmlDeviceSetPowerManagementLimit(self, h, mw): self.cap = mw // 1000; self.set_calls.append(self.cap)
    def nvmlDeviceGetTotalEnergyConsumption(self, h):
        self.e += 1000 * (self.cap / 700) * 50   # lower cap -> less energy per answer
        return self.e


def cfg():
    return {"node": {"cap_min_w": 300, "cap_max_w": 375, "approval": "manual", "sam": False}}


def test_live_check_keeps_caps_while_answers_match_then_rolls_back(tmp_path):
    g = FakeGpu()
    def ask(p):    # answers change below 325 W
        return "wrong" if g.cap < 325 else "42"
    out = agent.run(cfg(), approve=True, gpu=g, log_path=tmp_path / "n.jsonl",
                    answer_check=AnswerCheck(ask, g, prompts=["q1", "q2"], p99_limit=1e9), samples_per_step=1)
    assert out["cap_w"] == 325 and g.cap == 325
    assert [r["accepted"] for r in out["results"]] == [True, True, True, False]


def test_without_a_check_a_real_gpu_rolls_back_to_stock(tmp_path):
    g = FakeGpu()
    out = agent.run(cfg(), approve=True, gpu=g, log_path=tmp_path / "n.jsonl", samples_per_step=1)
    assert out["cap_w"] == 700 and g.cap == 700


def test_any_error_restores_stock(tmp_path):
    g = FakeGpu()
    class Boom(AnswerCheck):
        def at(self, cap): raise RuntimeError("upstream down")
    with pytest.raises(RuntimeError):
        agent.run(cfg(), approve=True, gpu=g, log_path=tmp_path / "n.jsonl", samples_per_step=1,
                  answer_check=Boom(lambda p: "1", g, prompts=["q"]))
    assert g.cap == 700


def test_rack_check_without_mask_decoder(monkeypatch):
    import wattnot.sam.room as room
    def nodecode(*a, **k): raise RuntimeError("install the vision extra")
    monkeypatch.setattr(room, "decode_mask", nodecode)
    class NoSam:
        def segment(self, *a, **k): raise OSError("no key")
    rc = agent.rack_check(NoSam())
    assert rc["ok"] and rc["visible_racks"] and "boxes only" in rc["note"]
