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
    assert r["accepted"] and r["pairs"] == 4 and r["n"] == 8 and g.w == 375
    assert -32 < r["energy_change_pct"] < -28
    assert r["quality"]["stock_pct"] == 100 and r["quality"]["cap_pct"] == 100


def test_reworded_but_correct_answers_pass():
    r, _ = _check(reword=True)
    assert r["accepted"], r["why_not"]


def test_rejects_wrong_answers_and_no_energy_drop():
    r, _ = _check(wrong_cap=1)
    assert not r["accepted"] and r["why_not"][0].startswith("quality")
    assert r["quality"]["holdout_paired"]["discordant"]["stock_only"] == 1
    assert any("energy" in w for w in _check(j_cap=1.0)[0]["why_not"])


def test_one_flip_never_refuses_cap_noninferiority():
    from wattnot.node.answercheck import paired_noninferior
    for n in (20, 111, 222):
        st = [True] * (n // 2) + [False] * (n - n // 2)
        cap = list(st); cap[0] = False
        assert paired_noninferior(st, cap)["passed"]


def test_noninferiority_refuses_a_real_drop():
    from wattnot.node.answercheck import paired_noninferior
    st = [True] * 100 + [False] * 11
    cap = [False] * 20 + [True] * 80 + [False] * 11
    assert not paired_noninferior(st, cap)["passed"]


def test_check_uses_holdout_max_tokens():
    from wattnot.node.answercheck import HOLDOUT_MAX_TOKENS
    from wattnot.longrun.holdout import FROZEN_STACK
    assert HOLDOUT_MAX_TOKENS == FROZEN_STACK["max_tokens"] == 1536


def test_check_runs_four_pairs_so_energy_uses_ci_rule():
    import inspect
    from wattnot.node.answercheck import CHECK_PAIRS, HarnessCapCheck
    from wattnot.proof.session import SMALL_N_MAX, energy_clearly_down
    assert inspect.signature(HarnessCapCheck).parameters["pairs"].default == CHECK_PAIRS == 4 > SMALL_N_MAX
    d = [-24.9, -37.0, -24.8, -23.9]            # the refused run-1 pairs plus two steady ones
    import statistics as st, math
    m, half = st.mean(d), 3.182 * st.stdev(d) / math.sqrt(4)
    assert energy_clearly_down(d, m, half, 20.0)


def test_gpu_warmup_runs_at_stock_before_check_and_is_labelled():
    r, g = _check()
    assert r["warmup"]["label"] == "GPU warm-up (not measured)" and r["warmup"]["cap_w"] == 700
    assert r["warmup"]["requests"] == 2 and r["warmup"]["burn_in_blocks"] == 1
    assert r["accepted"]


class G2(G):
    """NVML view with a readable power limit; `other` is a second GPU job that resets it."""
    def __init__(self):
        super().__init__()
        self.other = set()        # block numbers in which the other job sets 700 W mid-block
        self.blocks = 0
    def nvmlDeviceGetPowerManagementLimit(self, _): return self.w * 1000


def _replay(other_blocks, cap_factors):
    import itertools
    from wattnot.node import answercheck as ac
    g = G2()
    fac = itertools.cycle(cap_factors)
    cur = {"f": 0.75}
    def set_cap(w):
        g.w = w
        g.blocks += 1
        if w != 700:
            cur["f"] = next(fac)
    k = [0]
    def ask(p, mt):
        time.sleep(0.004)
        k[0] += 1
        if g.blocks in g.other and k[0] % 8 == 4:
            g.w = 700                 # the traffic run's own check resets the limit
        g.e += int(1000 * (1.0 if g.w == 700 else cur["f"]))
        return f"#### {ITEMS[p]}"
    g.other = set(other_blocks)
    c = ac.HarnessCapCheck(ask, g, set_cap, 700, n=8, concurrency=4)
    c.warm = {"label": "GPU warm-up (not measured)"}
    return c.at(375)


def test_take4_contaminated_pairs_are_dropped_and_replaced(monkeypatch):
    from wattnot.node import answercheck as ac
    monkeypatch.setattr(ac, "SAMPLE_S", 0.002)
    # blocks are numbered by set_cap calls; pairs 1-3 have a block where the limit is reset
    r = _replay(other_blocks={2, 3, 6}, cap_factors=[0.75, 0.73, 0.75, 0.77])
    assert len(r["dropped_pairs"]) >= 2
    assert all("power limit changed mid-block" in d and d.startswith("pair ") for d in r["dropped_pairs"])
    assert r["pairs"] == 4 and r["pairs_run"] == 4 + len(r["dropped_pairs"])
    assert r["accepted"] and r["energy_change_pct"] < -20


def test_clean_runs_stay_accepted_with_no_drops(monkeypatch):
    from wattnot.node import answercheck as ac
    monkeypatch.setattr(ac, "SAMPLE_S", 0.002)
    for f in ([0.746, 0.727, 0.746, 0.768], [0.75, 0.797, 0.723, 0.769]):   # warmup-0503, take-4 check
        r = _replay(other_blocks=set(), cap_factors=f)
        assert r["dropped_pairs"] == [] and r["pairs"] == 4 and r["accepted"]


def test_extra_pairs_are_capped():
    from wattnot.node import answercheck as ac
    r = _replay(other_blocks=set(range(1, 40)), cap_factors=[0.75])
    assert r["pairs_run"] == 4 + ac.MAX_EXTRA_PAIRS and r["pairs"] == 0 and not r["accepted"]


def test_counter_vs_samples_check():
    from wattnot.node.answercheck import block_validity
    assert block_validity(375, 375, 375, [(375, 300)] * 5, 3000, 10)["valid"]
    v = block_validity(375, 375, 375, [(375, 300)] * 5, 4000, 10)
    assert not v["valid"] and "differ by" in v["why"]
    assert not block_validity(375, 375, 700, [], 1, 1)["valid"]
