"""Adaptive top-k: the optional MoE trial. Everything here runs on fakes (no GPU, no paid call)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from wattnot import runbook as R
from wattnot.approvals import model as m
from wattnot.approvals.gate import ApprovalGate, NotApproved
from wattnot.approvals.proposers import from_moe_receipt
from wattnot.approvals.service import Approvals
from wattnot.moe import adaptive_topk as at
from wattnot.moe.adaptive_topk import AdaptiveConfig, AdaptiveError, FakeMoeBackend
from wattnot.verify import verify_receipt

ROOT = Path(__file__).resolve().parent.parent
QUICK = R.Plan(caps=(700, 600, 500), reps=5, repeat=1, concurrency=1, ablation_trials=4)
BOUNDS = {"moe_routing": {"min_mass": 0.5, "max_mass": 0.999}}


class Clock:
    """A clock that only moves when the fake backend 'runs', so time-box tests do not sleep."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class TimedFake(FakeMoeBackend):
    def __init__(self, clock, per_generate=10.0, **kw):
        super().__init__(**kw)
        self.clock, self.per = clock, per_generate

    def generate(self, prompts):
        self.clock.t += self.per
        return super().generate(prompts)


def gate(tmp_path):
    return ApprovalGate(Approvals(tmp_path / "p"), mode="pre_approved", name="t",
                        announce=lambda _m: None)


# ---- the rule --------------------------------------------------------------------------------

def test_kept_slots_rule():
    w = [0.5, 0.3, 0.15, 0.03, 0.01, 0.01]
    assert at.kept_slots(w, 0.9) == 3      # 0.5 + 0.3 + 0.15 passes 0.9, so the rest are dropped
    assert at.kept_slots(w, 0.6) == 2
    assert at.kept_slots(w, 0.999) == 6
    assert at.kept_slots([0.99, 0.01], 0.5) == 1      # the first slot is always kept
    assert at.kept_slots([0.25] * 4, 0.9) == 4        # a flat router keeps everything
    assert at.kept_slots([], 0.9) == 0


def test_config_validates_mass_and_sizes():
    AdaptiveConfig(mass=0.9)
    for bad in (0.2, 1.0, 1.5):
        with pytest.raises(ValueError):
            AdaptiveConfig(mass=bad)
    with pytest.raises(ValueError):
        AdaptiveConfig(batch=16, n_prompts=8)


def test_p99_is_nearest_rank():
    assert at.p99(list(range(1, 101))) == 99
    assert at.p99([5.0]) == 5.0


# ---- the trial -------------------------------------------------------------------------------

def test_fake_trial_is_simulated_and_alternates_arms():
    res = at.run_trial(FakeMoeBackend(), AdaptiveConfig())
    assert res.simulated and at.label_of(res) == "SIMULATED"
    assert [r.arm for r in res.runs][:4] == ["off", "on", "on", "off"]  # ABBA, not AAAA
    s = at.summarize(res)
    assert len(res.runs) == 10 and s["pairs"] == 5 and s["all_gates_passed"]
    assert 5 < s["saving_pct"] < 20 and s["pairs_on_lower"] == 5
    assert s["off"]["active_experts"] == pytest.approx(8.0) and s["on"]["active_experts"] < 8


def test_trial_needs_a_grant_for_the_knob(tmp_path):
    g = gate(tmp_path)
    backend = FakeMoeBackend()
    with g.session(title="caps only", bounds={"power_cap_w": {"min": 400, "max": 700}}) as s:
        with pytest.raises(NotApproved):
            at.run_trial(backend, AdaptiveConfig(), session=s)
    assert not backend.closed  # refused before anything started


def test_trial_inside_the_grant_runs_and_is_counted(tmp_path):
    g = gate(tmp_path)
    with g.session(title="moe", bounds=BOUNDS) as s:
        at.run_trial(FakeMoeBackend(), AdaptiveConfig(mass=0.8), session=s)
        gid = s.id
    usage = g.approvals.get(gid)["applied"]["usage"]
    assert usage["by_kind"]["moe_routing"]["max"] == 0.8


def test_grant_refuses_a_mass_outside_its_range(tmp_path):
    with gate(tmp_path).session(title="moe", bounds={"moe_routing": {
            "min_mass": 0.8, "max_mass": 0.95}}) as s, pytest.raises(NotApproved):
        s.check_moe(0.6)


def test_time_box_stops_cleanly_with_whole_pairs():
    clk = Clock()
    cfg = AdaptiveConfig(budget_s=150, reps=5)  # 4 batches x 10 s = 40 s per arm run
    be = TimedFake(clk)
    res = at.run_trial(be, cfg, clock=clk)
    assert res.truncated and "time box" in res.truncated
    arms = [r.arm for r in res.runs]
    assert arms.count("off") == arms.count("on") >= 1 and arms.count("off") < 5
    assert be.closed and res.seconds <= 150 + 1
    at.build_receipt(res)  # a cut-short run still gets an honest receipt


def test_zero_pairs_is_an_error_and_still_closes():
    clk = Clock()
    be = TimedFake(clk)
    res = at.run_trial(be, AdaptiveConfig(budget_s=30), clock=clk)
    assert res.runs == [] and res.truncated and be.closed
    with pytest.raises(AdaptiveError):
        at.summarize(res)


def _arm_run(arm, wh):
    return at.ArmRun(arm=arm, rep=0, wh=wh, seconds=1.0, tokens=100, requests=1,
                     latencies_ms=[10.0, 11.0], label="SIMULATED", method="fake",
                     csv_path="", csv_sha256="", active_experts=None)


def test_zero_baseline_energy_is_a_clear_error_not_zero_division():
    # A degenerate "off" arm with no measured energy would make the saving denominator 0.
    # summarize must raise AdaptiveError, not a bare ZeroDivisionError.
    res = at.TrialResult(cfg=AdaptiveConfig(), simulated=True, gpu="fake", driver="fake",
                         runs=[_arm_run("off", 0.0), _arm_run("on", 0.5)],
                         agreement={"pct": 99.0})
    with pytest.raises(AdaptiveError, match="zero joules"):
        at.summarize(res)


def test_backend_failure_closes_the_backend():
    be = FakeMoeBackend(fail="generate")
    with pytest.raises(AdaptiveError):
        at.run_trial(be, AdaptiveConfig())
    assert be.closed


# ---- the receipt -----------------------------------------------------------------------------

def test_receipt_validates_verifies_and_is_honest(tmp_path):
    res = at.run_trial(FakeMoeBackend(), AdaptiveConfig())
    w = at.write_receipt(res, tmp_path, sign_key=_key(tmp_path))
    r, path = w["receipt"], Path(w["path"])
    assert path.name.startswith("rcpt_") and r["measurement"]["label"] == "SIMULATED"
    assert r["grade"] == "SIMULATED" and r["profile"] == "moe_adaptive_topk"
    text = path.read_text()
    assert "MEASURED" not in text and "novel" not in text.lower()
    blk = r["moe_adaptive_topk"]
    assert blk["known_technique"] and blk["vllm_native"] is False and blk["default"] == "off"
    assert any("2402.14800" in p for p in blk["prior_art"])
    assert "joules per good answer" not in text and r["result"]["welch"]["applicable"] is False
    rep = verify_receipt(path)
    assert all(st.ok for st in rep.steps if st.name != "rekor")
    assert (tmp_path / "adaptive_topk_runs.json").exists()


def test_a_real_run_whose_counter_did_not_move_gets_no_receipt():
    res = at.run_trial(FakeMoeBackend(), AdaptiveConfig())
    res.simulated = False
    for r in res.runs:
        r.label, r.method = "ESTIMATE", "model"
    assert at.label_of(res) == "ESTIMATE"
    with pytest.raises(AdaptiveError):
        at.build_receipt(res)


def test_a_real_run_with_every_counter_window_is_measured():
    res = at.run_trial(FakeMoeBackend(), AdaptiveConfig())
    res.simulated = False
    for r in res.runs:
        r.label, r.method = "MEASURED", "counter"
    assert at.build_receipt(res)["measurement"]["label"] == "MEASURED"


def _key(tmp_path):
    from wattnot.receipt.sign import generate_test_key

    k = tmp_path / "k.pem"
    generate_test_key(k)
    return str(k)


# ---- the proposal ----------------------------------------------------------------------------

def test_proposal_is_pending_and_off_until_a_person_applies_it(tmp_path):
    apps = Approvals(tmp_path / "p")
    res = at.run_trial(FakeMoeBackend(), AdaptiveConfig())
    r = at.build_receipt(res)
    p = apps.propose(from_moe_receipt(r))
    assert p["kind"] == m.MOE_ROUTING and p["status"] == m.PENDING
    assert p["change"]["after"]["adaptive_topk"] is True and p["change"]["before"] == {
        "adaptive_topk": False}
    assert {s["label"] for s in p["savings"]} == {"SIMULATED"}
    assert at.read_setting(tmp_path / "p") is None
    apps.approve(p["id"], "akash")
    assert at.read_setting(tmp_path / "p") is None   # approved is not applied
    apps.apply(p["id"])
    assert at.read_setting(tmp_path / "p") == at.DEFAULT_MASS
    apps.rollback(p["id"], "akash")
    assert at.read_setting(tmp_path / "p") is None
    assert apps.verify() == []


def test_failed_quality_gate_is_a_result_not_a_proposal(tmp_path):
    out = tmp_path / "o"
    plan = R.Plan(**{**QUICK.__dict__, "adaptive_min_agree": 99.0})
    code = R.run(R.FakeRig(), out, plan=plan, stages=("setup", "probe", "adaptive_topk"),
                 key_path=tmp_path / "k.pem", run_tag="t")
    info = json.loads((out / "summary.json").read_text())["adaptive_topk"]
    assert code == 0 and info["proposal"] is None and info["not_proposed"] == ["quality"]
    assert list((out / "adaptive_topk").glob("rcpt_*.json"))


# ---- the runbook and the script --------------------------------------------------------------

def _run(tmp_path, stages, **rig):
    out = tmp_path / "o"
    code = R.run(R.FakeRig(**rig), out, plan=QUICK, stages=stages, key_path=tmp_path / "k.pem",
                 run_tag="t")
    return code, out, json.loads((out / "summary.json").read_text())


def test_off_by_default():
    assert "adaptive_topk" not in R.STAGES and "adaptive_topk" not in R.KIT_STAGES
    assert R.ORDER[-1] == "adaptive_topk" and "adaptive_topk" in R.OPTIONAL
    assert "moe_routing" not in R._session_bounds(R.FakeRig(), R.Plan(), R.STAGES)
    assert "moe_routing" in R._session_bounds(R.FakeRig(), R.Plan(), (*R.STAGES, "adaptive_topk"))


def test_runbook_with_the_stage_writes_a_simulated_receipt_and_a_pending_proposal(tmp_path):
    code, out, s = _run(tmp_path, ("setup", "probe", "adaptive_topk"))
    assert code == 0 and s["stages"]["adaptive_topk"]["ok"]
    a = s["adaptive_topk"]
    assert a["label"] == "SIMULATED" and a["known_technique"] and a["proposal"]
    assert s["receipt_labels"] == {"SIMULATED": 1}
    assert a["proposal"] in s["approvals"]["proposals"]
    assert (out / "approvals" / f"{a['proposal']}.json").exists()
    prop = json.loads((out / "approvals" / f"{a['proposal']}.json").read_text())
    assert prop["status"] == "pending"   # even under a pre-approved trial grant


@pytest.mark.parametrize("fail", ["prepare", "generate"])
def test_a_failing_stage_does_not_change_the_exit_code(tmp_path, fail):
    out = tmp_path / "o"
    rig = R.FakeRig()
    rig.adaptive_fail = fail
    code = R.run(rig, out, plan=QUICK, stages=("setup", "probe", "flex", "adaptive_topk"),
                 key_path=tmp_path / "k.pem", run_tag="t")
    s = json.loads((out / "summary.json").read_text())
    assert code == 0
    assert s["stages"]["flex"]["ok"] and not s["stages"]["adaptive_topk"]["ok"]
    assert s["stages"]["adaptive_topk"]["optional"] and "simulated failure" in s[
        "stages"]["adaptive_topk"]["error"]
    assert s["receipt_labels"] == {"SIMULATED": 3}  # flex receipts only; none from the failure


def test_core_failures_still_use_their_exit_codes(tmp_path):
    code, _, s = _run(tmp_path, ("setup", "probe", "adaptive_topk"), energy_counter=False)
    assert code == R.EXIT_PROBE and "adaptive_topk" not in s["stages"]  # skipped, not run
    code, *_ = _run(tmp_path / "x", ("setup", "probe"), cap_writable=False)
    assert code == R.EXIT_SETUP


def test_without_a_grant_for_the_stage_nothing_runs(tmp_path):
    """A run that does not list the stage never asks for the knob."""
    _, _, s = _run(tmp_path, ("setup", "probe"))
    assert "adaptive_topk" not in s


def test_cli_flag_adds_the_stage_and_checks_the_mass(tmp_path):
    out = tmp_path / "c"
    assert R.main(["--rig", "fake", "--out", str(out), "--stages", "setup,probe",
                   "--adaptive-topk", "--adaptive-mass", "0.8", "--quick"]) == 0
    s = json.loads((out / "summary.json").read_text())
    assert s["adaptive_topk"]["mass"] == 0.8
    with pytest.raises(SystemExit):
        R.main(["--rig", "fake", "--out", str(tmp_path / "d"), "--adaptive-topk",
                "--adaptive-mass", "1.3"])


def _sh(tmp_path, *args):
    env = {"PATH": "/usr/bin:/bin:" + str(Path(sys.executable).parent),
           "PYTHONPATH": str(ROOT / "src"), "HOME": str(tmp_path),
           "PYTHON": sys.executable}
    return subprocess.run(["bash", str(ROOT / "scripts" / "h100_oct30.sh"), *args],
                          capture_output=True, text=True, env=env, cwd=ROOT, check=False)


def test_script_help_and_bad_mass(tmp_path):
    h = _sh(tmp_path, "--help")
    assert h.returncode == 0 and "--adaptive-topk" in h.stdout and "Exit codes" in h.stdout
    bad = _sh(tmp_path, "--fake", "--adaptive-topk", "--adaptive-mass", "1.2",
              "--out", str(tmp_path / "x"))
    assert bad.returncode == 2 and "--adaptive-mass" in bad.stdout


def test_script_fake_dry_run_with_the_flag_exits_0(tmp_path):
    out = tmp_path / "r"
    p = _sh(tmp_path, "--fake", "--adaptive-topk", "--out", str(out))
    assert p.returncode == 0, p.stdout + p.stderr
    s = json.loads((out / "summary.json").read_text())
    assert s["adaptive_topk"]["label"] == "SIMULATED" and set(s["receipt_labels"]) == {"SIMULATED"}
    plain = tmp_path / "plain"
    assert _sh(tmp_path, "--fake", "--out", str(plain)).returncode == 0
    assert "adaptive_topk" not in json.loads((plain / "summary.json").read_text())["stages"]


# ---- the real hook, on a tiny random model (skipped without torch + transformers) -----------

def test_torch_hook_off_matches_stock_and_on_uses_fewer_experts():
    torch = pytest.importorskip("torch")
    tf = pytest.importorskip("transformers")
    if not hasattr(tf, "GraniteMoeConfig"):
        pytest.skip("this transformers has no GraniteMoE")
    from wattnot.moe.hf_backend import find_moe_layers, install_hooks

    torch.manual_seed(0)
    cfg = tf.GraniteMoeConfig(
        vocab_size=200, hidden_size=32, intermediate_size=48, num_hidden_layers=2,
        num_attention_heads=4, num_key_value_heads=2, num_local_experts=8,
        num_experts_per_tok=4, max_position_embeddings=64)
    model = tf.GraniteMoeForCausalLM(cfg).eval()
    ids = torch.randint(0, 200, (2, 12))
    with torch.no_grad():
        stock = model(ids).logits
    if not all(hasattr(x, "router") for x in model.modules() if "MoE" in type(x).__name__):
        pytest.skip("this transformers uses a different MoE layout")
    assert len(find_moe_layers(model)) == 2
    st, restore = install_hooks(model, torch)
    with torch.no_grad():
        st.mass = None
        off = model(ids).logits
        assert torch.allclose(off, stock, atol=1e-5)          # same code path, no threshold
        assert st.used.item() / st.slots == 4.0
        st.reset(torch)
        st.mass = 0.5
        on = model(ids).logits
        assert st.used.item() / st.slots < 4.0                # fewer experts per token
        assert not torch.allclose(on, stock, atol=1e-5)       # and a different model
    restore()
    with torch.no_grad():
        assert torch.equal(model(ids).logits, stock)


def test_router_output_order_is_found_by_shape_and_dtype():
    """transformers 5.19 returns (index, weights, logits); 5.21 returns (logits, weights, index)."""
    torch = pytest.importorskip("torch")
    from wattnot.moe.hf_backend import _router_order

    logits, w, idx = torch.randn(5, 40), torch.rand(5, 8), torch.randint(0, 40, (5, 8))
    assert _router_order((logits, w, idx), torch) == (0, 1, 2)
    assert _router_order((idx, w, logits), torch) == (2, 1, 0)
    with pytest.raises(AdaptiveError):
        _router_order((logits, w, w), torch)
