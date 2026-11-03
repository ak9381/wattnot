"""The live cap check uses the H100 sweep's rule: interleaved blocks, mean p99 <= +10%,
energy_clearly_down, proof-v1 prompts at concurrency 32; answer match unchanged."""
from wattnot.node.answercheck import HarnessCapCheck, proof_prompts


class G:
    def __init__(self): self.e, self.w = 0, 700
    def nvmlDeviceGetTotalEnergyConsumption(self, _): return self.e


def _check(slow_pct=0.0, j_cap=0.7, wrong=False):
    g = G()
    def set_cap(w): g.w = w
    k = [0]
    def ask(p, mt):
        k[0] += 1
        g.e += int(1000 * (1.0 if g.w == 700 else j_cap) * (1 + 0.03 * (k[0] % 3)))   # some noise
        return "x" if (wrong and g.w != 700) else "42"
    c = HarnessCapCheck(ask, g, set_cap, 700, n=8, concurrency=4)
    return c.at(375), g


def test_uses_proof_v1_and_accepts_a_good_cap():
    assert len(proof_prompts()) == 111
    r, g = _check()
    assert r["accepted"] and r["pairs"] == 2 and r["n"] == 8 and g.w == 375
    assert -32 < r["energy_change_pct"] < -28


def test_rejects_changed_answers_and_no_energy_drop():
    assert "answers changed" in _check(wrong=True)[0]["why_not"][0]
    assert any("energy" in w for w in _check(j_cap=0.99)[0]["why_not"])
