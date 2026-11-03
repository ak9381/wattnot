"""The live cap check uses the H100 sweep's rule: interleaved blocks, mean p99 <= +10%,
energy_clearly_down, proof-v1 prompts at concurrency 32, and quality scored by the harness
graders (fixed_checks: same number / word match), not exact text."""
import time

from wattnot.node.answercheck import HarnessCapCheck, proof_prompts

ITEMS = {p["prompt"]: p["checks"][0]["value"] for p in proof_prompts(8)}


class G:
    def __init__(self): self.e, self.w = 0, 700
    def nvmlDeviceGetTotalEnergyConsumption(self, _): return self.e


def _check(j_cap=0.7, wrong_cap=0, reword=False):
    g = G()
    def set_cap(w): g.w = w
    k = [0]
    def ask(p, mt):
        time.sleep(0.05)   # steady latency so p99 jitter stays small
        k[0] += 1
        g.e += int(1000 * (1.0 if g.w == 700 else j_cap) * (1 + 0.03 * (k[0] % 3)))   # some noise
        v = ITEMS[p]
        if g.w != 700 and list(ITEMS).index(p) < wrong_cap:
            v = "-99999"
        return (f"Different words, step by step, same result.\n#### {v}" if reword and g.w != 700
                else f"#### {v}")
    c = HarnessCapCheck(ask, g, set_cap, 700, n=8, concurrency=4)
    return c.at(375), g


def test_uses_proof_v1_and_accepts_a_good_cap():
    assert len(proof_prompts()) == 111
    r, g = _check()
    assert r["accepted"] and r["pairs"] == 2 and r["n"] == 8 and g.w == 375
    assert -32 < r["energy_change_pct"] < -28
    assert r["quality"]["stock_pct"] == 100 and r["quality"]["cap_pct"] == 100


def test_reworded_but_correct_answers_pass():
    r, _ = _check(reword=True)
    assert r["accepted"], r["why_not"]


def test_rejects_wrong_answers_and_no_energy_drop():
    r, _ = _check(wrong_cap=1)
    assert not r["accepted"] and r["why_not"][0].startswith("quality")
    assert r["quality"]["holdout_paired"]["discordant"]["stock_only"] == 1
    assert any("energy" in w for w in _check(j_cap=0.99)[0]["why_not"])
