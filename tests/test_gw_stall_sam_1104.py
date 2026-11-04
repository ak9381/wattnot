"""Nov 4 take fixes: the answer check between the arms reports which block it is on (the UI
looked stuck on the without arm for ~12 min), and the GPU helper result carries the SAM label."""
from wattnot.node import agent
from wattnot.node.answercheck import HarnessCapCheck, proof_prompts

ITEMS = {p["prompt"]: p["checks"][0]["value"] for p in proof_prompts(8)}


def test_answer_check_reports_each_block():
    g = type("G", (), {"e": 0, "w": 700, "nvmlDeviceGetTotalEnergyConsumption": lambda s, _: s.e})()

    def set_cap(w): g.w = w

    def ask(p, mt):
        g.e += 1000 if g.w == 700 else 700
        return f"#### {ITEMS[p]}"
    c = HarnessCapCheck(ask, g, set_cap, 700, n=8, concurrency=4)
    c.warm = {"skipped": True}
    seen = []
    c.progress = seen.append
    c.at(375)
    pairs = [s for s in seen if s.startswith("pair ")]
    assert pairs[0] == "pair 1 of 4, stock block at 700 W" and len(pairs) >= 8
    assert any("cap block at 375 W" in s for s in pairs)


def test_progress_callback_errors_never_break_the_check():
    c = HarnessCapCheck(lambda p, m: "", None, lambda w: None, 700, n=2)
    c.progress = lambda s: 1 / 0
    c._note("x")   # no raise


def test_waiting_approval_result_carries_rack_label(tmp_path):
    class Gpu:
        label = "MEASURED"
        def nvmlDeviceGetName(self, h): return "H100"
        def nvmlDeviceGetCount(self): return 1
        def nvmlDeviceGetPowerUsage(self, h): return 300_000
        def nvmlDeviceGetPowerManagementLimit(self, h): return 700_000
        def nvmlDeviceGetTotalEnergyConsumption(self, h): return 0

    class NoSam:
        def segment(self, *a, **k): raise OSError("no key")
    out = agent.run({"node": {"cap_min_w": 300, "cap_max_w": 375, "approval": "manual"}},
                    gpu=Gpu(), sam_client=NoSam(), log_path=tmp_path / "n.jsonl", samples_per_step=1)
    assert out["status"] == "waiting_approval" and out["rack_label"].startswith("REPLAY")
