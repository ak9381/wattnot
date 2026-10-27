"""Approve before apply: store, audit trail, service, policy, appliers, gate, proposers.

Everything runs on fakes: a fake clock, a fake NVML, temp folders. Nothing real is changed
and nothing is paid for.
"""

from __future__ import annotations

import copy
import json

import pytest

from fixtures.nvml_fake import FakeNVML
from wattnot.approvals import (
    ApprovalGate,
    Approvals,
    CallbackApplier,
    NotApproved,
    PowerCapApplier,
    ProposalError,
    StateError,
)
from wattnot.approvals import model as m
from wattnot.approvals import policy as pol
from wattnot.approvals.active import read_active
from wattnot.approvals.appliers import ApplyError, nvml_appliers
from wattnot.approvals.proposers import checks_from_gates, flex_shed_spec, from_cloud_receipt
from wattnot.gates.result import GateResult


class Clock:
    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def cap_spec(**over):
    spec = {
        "kind": m.POWER_CAP, "title": "Cap GPU 0 at 500 W",
        "summary": "Lower the power cap from 700 W to 500 W.",
        "change": {"target": "gpu:0", "gpu": 0, "before": {"power_cap_w": 700},
                   "after": {"power_cap_w": 500}, "drop_pct": 28.6},
        "savings": [{"metric": "energy per request", "value": 9.5, "unit": "%",
                     "label": "MEASURED"}],
        "checks": {"quality": {"passed": True, "detail": "pass rate held"},
                   "p99": {"passed": True, "detail": "within budget", "increase_pct": 4.0}},
        "receipt_preview": {"profile": "gpu", "label": "MEASURED"},
        "ttl_s": 3600,
    }
    spec.update(over)
    return spec


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def apps(tmp_path, clock):
    return Approvals(tmp_path / "p", clock=clock)


@pytest.fixture
def nvml():
    return FakeNVML(power_limit_mw=700_000)


# ---- proposal lifecycle -------------------------------------------------------------------

def test_a_new_proposal_is_pending_and_hashed(apps):
    p = apps.propose(cap_spec())
    assert p["status"] == m.PENDING and p["label"] == "MEASURED"
    assert p["content_sha256"] == m.content_hash(p)
    assert apps.get(p["id"])["status"] == m.PENDING


def test_nothing_is_applied_until_approved(apps, nvml):
    apps.appliers.update(nvml_appliers(nvml))
    p = apps.propose(cap_spec())
    with pytest.raises(StateError, match="not approved|approved"):
        apps.apply(p["id"])
    assert nvml.power_history == []


def test_approve_then_apply_then_rollback(apps, nvml):
    apps.appliers.update(nvml_appliers(nvml))
    p = apps.propose(cap_spec())
    apps.approve(p["id"], "user:akash", note="ok")
    assert nvml.power_history == []  # approving alone changes nothing
    done = apps.apply(p["id"], by="user:akash")
    assert done["status"] == m.APPLIED and nvml.current_limit_mw == 500_000
    back = apps.rollback(p["id"], "user:akash")
    assert back["status"] == m.ROLLED_BACK and nvml.current_limit_mw == 700_000


def test_reject_is_final(apps):
    p = apps.propose(cap_spec())
    apps.reject(p["id"], "user:akash", note="no")
    with pytest.raises(StateError):
        apps.approve(p["id"], "user:akash")


def test_approval_needs_the_hash_the_person_reviewed(apps):
    p = apps.propose(cap_spec())
    with pytest.raises(StateError, match="changed"):
        apps.approve(p["id"], "user:a", expect_sha256="deadbeef")
    ok = apps.approve(p["id"], "user:a", expect_sha256=p["content_sha256"][:12])
    assert ok["decision"]["content_sha256"] == p["content_sha256"]


def test_editing_the_stored_proposal_blocks_apply(apps, nvml):
    apps.appliers.update(nvml_appliers(nvml))
    p = apps.propose(cap_spec())
    apps.approve(p["id"], "user:a")
    raw = json.loads((apps.store.root / "proposals" / f"{p['id']}.json").read_text())
    raw["change"]["after"]["power_cap_w"] = 300  # someone edits the file after approval
    (apps.store.root / "proposals" / f"{p['id']}.json").write_text(json.dumps(raw))
    with pytest.raises(StateError, match="no longer matches"):
        apps.apply(p["id"])
    assert nvml.power_history == []


def test_proposals_expire(apps, clock):
    p = apps.propose(cap_spec(ttl_s=60))
    clock.t += 61
    assert apps.get(p["id"])["status"] == m.EXPIRED
    with pytest.raises(StateError):
        apps.approve(p["id"], "user:a")


def test_an_approved_but_unapplied_proposal_also_expires(apps, clock):
    p = apps.propose(cap_spec(ttl_s=60))
    apps.approve(p["id"], "user:a")
    clock.t += 61
    with pytest.raises(StateError):
        apps.apply(p["id"])
    assert apps.get(p["id"])["status"] == m.EXPIRED


@pytest.mark.parametrize("bad", [
    {"kind": "nope"},
    {"title": ""},
    {"change": {}},
    {"savings": [{"metric": "x", "value": 1}]},                        # no label
    {"savings": [{"metric": "x", "value": 1, "label": "GREAT"}]},      # made-up label
    {"checks": {"quality": {"detail": "no verdict"}}},
    {"ttl_s": -5},
])
def test_invalid_proposals_are_refused(apps, bad):
    with pytest.raises(ProposalError):
        apps.propose(cap_spec(**bad))
    assert apps.list() == []


def test_label_is_the_weakest_saving_label(apps):
    s = cap_spec(savings=[{"metric": "a", "value": 1, "unit": "%", "label": "MEASURED"},
                          {"metric": "b", "value": 2, "unit": "Wh", "label": "ESTIMATE"}])
    assert apps.propose(s)["label"] == "ESTIMATE"


def test_apply_failure_is_recorded_and_hardware_is_left_alone(apps, nvml):
    nvml.allow_cap_write = False
    apps.appliers.update(nvml_appliers(nvml))
    p = apps.propose(cap_spec())
    apps.approve(p["id"], "user:a")
    with pytest.raises(ApplyError):
        apps.apply(p["id"])
    assert apps.get(p["id"])["status"] == m.FAILED
    assert nvml.current_limit_mw == 700_000


def test_power_cap_apply_checks_the_readback():
    state = {"cap": 700}

    def set_cap(gpu, w):
        state["cap"] = max(w, 600)  # the card will not go below 600 W
        return state["cap"]

    ap = PowerCapApplier(lambda gpu: state["cap"], set_cap)
    with pytest.raises(ApplyError, match="reports 600 W"):
        ap.apply({"change": {"gpu": 0, "after": {"power_cap_w": 500}}})
    assert state["cap"] == 700  # put back


# ---- audit trail ---------------------------------------------------------------------------

def test_audit_records_who_when_and_the_hash(apps, nvml):
    apps.appliers.update(nvml_appliers(nvml))
    p = apps.propose(cap_spec())
    apps.approve(p["id"], "user:akash", via="cli", note="ok")
    apps.apply(p["id"], by="user:akash")
    events = apps.audit(p["id"])
    assert [e["event"] for e in events] == ["proposed", "approved", "applied"]
    appr = events[1]
    assert appr["actor"] == "user:akash" and appr["via"] == "cli"
    assert appr["content_sha256"] == p["content_sha256"] and appr["ts"].endswith("Z")
    assert apps.verify() == []


def test_audit_chain_detects_tampering(apps):
    apps.propose(cap_spec())
    p2 = apps.propose(cap_spec())
    apps.approve(p2["id"], "user:a")
    assert apps.verify() == []
    f = apps.store.audit_path
    lines = f.read_text().splitlines()
    doc = json.loads(lines[1])
    doc["actor"] = "user:someone-else"
    lines[1] = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    f.write_text("\n".join(lines) + "\n")
    assert apps.verify()


def test_audit_chain_detects_a_removed_entry(apps):
    for _ in range(3):
        apps.propose(cap_spec())
    f = apps.store.audit_path
    lines = f.read_text().splitlines()
    f.write_text("\n".join([lines[0], lines[2]]) + "\n")
    assert apps.verify()


def test_audit_catches_a_status_that_the_log_does_not_explain(apps):
    p = apps.propose(cap_spec())
    raw = json.loads((apps.store.root / "proposals" / f"{p['id']}.json").read_text())
    raw["status"] = m.APPROVED  # flipped by hand, no audit entry
    (apps.store.root / "proposals" / f"{p['id']}.json").write_text(json.dumps(raw))
    assert any("status" in x for x in apps.verify())


def test_receipt_link_is_by_hash_and_in_the_audit(apps, nvml):
    apps.appliers.update(nvml_appliers(nvml))
    p = apps.propose(cap_spec())
    apps.approve(p["id"], "user:a")
    apps.apply(p["id"])
    receipt = {"receipt_id": "rcpt_x", "grade": "VALID",
               "integrity": {"sha256": "ab" * 32, "signer": "key"}}
    out = apps.link_receipt(p["id"], receipt)
    assert out["receipt"]["sha256"] == "ab" * 32 and out["receipt"]["signed"] is True
    assert apps.audit(p["id"])[-1]["event"] == "receipt_linked"
    assert apps.verify() == []
    with pytest.raises(StateError):
        apps.link_receipt(p["id"], {"receipt_id": "x"})


def test_evidence_receipt_is_linked_when_applied(apps, nvml):
    apps.appliers.update(nvml_appliers(nvml))
    spec = cap_spec()
    spec["receipt_preview"] = {"profile": "gpu", "label": "MEASURED",
                               "evidence_receipt": {"receipt_id": "rcpt_e", "sha256": "cd" * 32,
                                                    "grade": "ESTIMATE", "signed": True}}
    p = apps.propose(spec)
    apps.approve(p["id"], "user:a")
    done = apps.apply(p["id"])
    assert done["receipt"]["sha256"] == "cd" * 32
    assert apps.audit(p["id"])[-1]["detail"]["receipt_sha256"] == "cd" * 32


def test_receipt_block_points_back_at_the_proposal(apps):
    p = apps.propose(cap_spec())
    apps.approve(p["id"], "user:a")
    blk = apps.receipt_block(p["id"])
    assert blk["proposal_sha256"] == p["content_sha256"] and blk["approved_by"] == "user:a"


# ---- auto-approve policy -------------------------------------------------------------------

POLICY = {"format": pol.FORMAT, "enabled": True, "allowed_labels": ["MEASURED"],
          "kinds": {"power_cap": {"min_saving_pct": 5, "max_p99_increase_pct": 10,
                                  "max_cap_drop_pct": 30}}, "max_per_day": 2}


def write_policy(apps, doc):
    apps.store.root.mkdir(parents=True, exist_ok=True)
    (apps.store.root / "policy.json").write_text(json.dumps(doc))


def test_auto_approve_is_off_by_default(apps):
    assert apps.policy().enabled is False
    assert apps.propose(cap_spec())["status"] == m.PENDING


def test_auto_approve_within_thresholds_says_so_in_the_audit(apps):
    write_policy(apps, POLICY)
    p = apps.propose(cap_spec())
    assert p["status"] == m.APPROVED
    assert p["decision"]["by"].startswith("auto-policy:") and p["decision"]["via"] == "auto-policy"
    assert p["decision"]["policy_sha256"]


@pytest.mark.parametrize("change", [
    lambda s: s["savings"][0].update(value=2.0),                          # under the minimum
    lambda s: s["savings"][0].update(label="SIMULATED"),                  # not a real measurement
    lambda s: s["savings"][0].update(label="ESTIMATE"),                   # label not allowed
    lambda s: s["checks"]["quality"].update(passed=False),
    lambda s: s["checks"].pop("p99"),
    lambda s: s["checks"]["p99"].update(increase_pct=25.0),
    lambda s: s["checks"]["p99"].pop("increase_pct"),
    lambda s: s["change"].update(drop_pct=60.0),
    lambda s: s["change"].pop("drop_pct"),
    lambda s: s.update(checks={}),
])
def test_auto_approve_stays_pending_when_any_threshold_fails(apps, change):
    write_policy(apps, POLICY)
    s = cap_spec()
    change(s)
    assert apps.propose(s)["status"] == m.PENDING


def test_auto_approve_daily_cap(apps):
    write_policy(apps, POLICY)
    assert [apps.propose(cap_spec())["status"] for _ in range(3)] == [
        m.APPROVED, m.APPROVED, m.PENDING]


def test_policy_cannot_list_simulated_or_mock_and_has_no_defaults():
    for bad in ("SIMULATED", "MOCK"):
        with pytest.raises(pol.PolicyError):
            pol.Policy.from_dict({**POLICY, "allowed_labels": [bad]})
    no_thresholds = {**POLICY, "kinds": {"power_cap": {"min_saving_pct": 5}}}
    with pytest.raises(pol.PolicyError, match="no defaults"):
        pol.Policy.from_dict(no_thresholds)
    with pytest.raises(pol.PolicyError):
        pol.Policy.from_dict({**POLICY, "kinds": {}})


def test_the_template_is_off():
    assert pol.Policy.from_dict(pol.TEMPLATE).enabled is False


def test_a_trial_grant_is_never_auto_approved(apps):
    write_policy(apps, POLICY)
    gate = ApprovalGate(apps, mode="ask", wait_s=0, poll_s=0, sleep=lambda s: None,
                        announce=lambda s: None)
    with pytest.raises(NotApproved):
        with gate.session(title="t", bounds={"power_cap_w": {"min": 400, "max": 700}}):
            pass


# ---- gate: single changes ------------------------------------------------------------------

def quiet_gate(apps, **kw):
    kw.setdefault("announce", lambda s: None)
    kw.setdefault("sleep", lambda s: None)
    return ApprovalGate(apps, poll_s=0, **kw)


def test_gate_without_approval_times_out_and_changes_nothing(apps, nvml):
    apps.appliers.update(nvml_appliers(nvml))
    gate = quiet_gate(apps, mode="ask", wait_s=0)
    with pytest.raises(NotApproved, match="not approved"):
        gate.request(cap_spec())
    assert nvml.power_history == []
    assert apps.list()[0]["status"] == m.REJECTED


def test_gate_applies_once_a_human_approves_while_it_waits(apps, nvml):
    apps.appliers.update(nvml_appliers(nvml))
    seen = []

    def human(_s):  # the person runs `wattnot proposals approve` while the gate polls
        pending = apps.list(m.PENDING)
        if pending:
            seen.append(pending[0]["id"])
            apps.approve(pending[0]["id"], "user:akash")

    gate = quiet_gate(apps, mode="ask", wait_s=30, sleep=human)
    done = gate.request(cap_spec())
    assert done["status"] == m.APPLIED and nvml.current_limit_mw == 500_000
    assert done["decision"]["by"] == "user:akash"


def test_dry_run_gate_only_takes_simulated_or_mock(apps):
    gate = quiet_gate(apps, mode="dry_run")
    applied = []
    ap = CallbackApplier(lambda p: applied.append(p["id"]), lambda t: None)
    with pytest.raises(NotApproved, match="SIMULATED or MOCK"):
        gate.request(cap_spec(), applier=ap)  # MEASURED
    assert applied == []
    sim = cap_spec(savings=[{"metric": "x", "value": 9, "unit": "%", "label": "SIMULATED"}])
    assert gate.request(sim, applier=ap)["status"] == m.APPLIED and len(applied) == 1


def test_pre_approved_needs_a_name_and_is_recorded(apps):
    with pytest.raises(ValueError):
        ApprovalGate(apps, mode="pre_approved", name="")
    ap = CallbackApplier(lambda p: {}, lambda t: None)
    gate = quiet_gate(apps, mode="pre_approved", name="oct30 script")
    done = gate.request(cap_spec(), applier=ap)
    assert done["decision"]["by"] == "pre-approved:oct30 script"
    assert done["decision"]["via"] == "pre-approved"


# ---- gate: trial sessions ------------------------------------------------------------------

BOUNDS = {"power_cap_w": {"min": 400, "max": 700}, "gpus": [0], "vllm_config": True,
          "flex_shed": {"max_target_pct": 30, "max_duration_s": 120}}


def test_session_allows_inside_bounds_and_counts_use(apps):
    gate = quiet_gate(apps, mode="pre_approved", name="run")
    with gate.session(title="try caps", bounds=BOUNDS) as s:
        s.check_cap(500, 0)
        s.check_cap(450, 0)
        s.check_vllm({"quantization": "fp8"})
        s.check_flex(25, 90)
        gid = s.id
    g = apps.get(gid)
    assert g["status"] == m.CLOSED
    usage = g["applied"]["usage"]
    assert usage["changes"] == 4 and usage["by_kind"]["power_cap"]["min"] == 450.0
    assert apps.verify() == []


@pytest.mark.parametrize("call", [
    lambda s: s.check_cap(300, 0),             # under the floor
    lambda s: s.check_cap(800, 0),             # over the ceiling
    lambda s: s.check_cap(500, 1),             # a GPU that is not in the grant
    lambda s: s.check_flex(50, 60),            # shed too deep
    lambda s: s.check_flex(20, 600),           # shed too long
])
def test_session_refuses_outside_bounds(apps, call):
    gate = quiet_gate(apps, mode="pre_approved", name="run")
    with gate.session(title="try caps", bounds=BOUNDS) as s, pytest.raises(NotApproved):
        call(s)


def test_session_refuses_kinds_it_was_not_granted(apps):
    gate = quiet_gate(apps, mode="pre_approved", name="run")
    with gate.session(title="caps only",
                      bounds={"power_cap_w": {"min": 400, "max": 700}}) as s:
        with pytest.raises(NotApproved):
            s.check_vllm({})
        with pytest.raises(NotApproved):
            s.check_flex(10, 10)


def test_session_without_approval_never_opens(apps):
    gate = quiet_gate(apps, mode="ask", wait_s=0)
    with pytest.raises(NotApproved):
        with gate.session(title="try caps", bounds=BOUNDS):
            raise AssertionError("the body must not run")


def test_a_revoked_grant_stops_the_next_change(apps, clock):
    gate = quiet_gate(apps, mode="pre_approved", name="run")
    with gate.session(title="try caps", bounds=BOUNDS) as s:
        s.check_cap(500, 0)
        apps.rollback(s.id, "user:akash", note="stop")  # revoke from the CLI or the API
        clock.t += 5
        with pytest.raises(NotApproved, match="stopping|rolled"):
            s.check_cap(500, 0)
    assert apps.get(s.id)["status"] == m.ROLLED_BACK  # stays revoked after close
    assert apps.verify() == []


def test_an_expired_grant_stops(apps, clock):
    gate = quiet_gate(apps, mode="pre_approved", name="run")
    with gate.session(title="try caps", bounds=BOUNDS, ttl_s=60) as s:
        s.check_cap(500, 0)
        clock.t += 61
        with pytest.raises(NotApproved, match="expired"):
            s.check_cap(500, 0)


def test_dry_run_session_is_marked_as_such(apps):
    gate = quiet_gate(apps, mode="dry_run")
    with gate.session(title="sim", bounds=BOUNDS) as s:
        assert s.mode == "dry_run"
    assert apps.get(s.id)["decision"]["via"] == "dry-run"


# ---- appliers: files -----------------------------------------------------------------------

def test_config_file_apply_and_rollback(apps):
    spec = {"kind": m.VLLM_CONFIG, "title": "vLLM fp8", "summary": "Restart with fp8.",
            "change": {"target": "vllm", "before": {"quantization": "none", "max_num_seqs": 256},
                       "after": {"quantization": "fp8", "max_num_seqs": 512},
                       "changed": ["quantization", "max_num_seqs"]},
            "savings": [{"metric": "energy per request", "value": 15, "unit": "%",
                         "label": "SIMULATED"}]}
    p = apps.propose(spec)
    assert read_active("vllm_config", apps.store.root) is None  # not before approval
    apps.approve(p["id"], "user:a")
    assert read_active("vllm_config", apps.store.root) is None  # not by approving alone
    apps.apply(p["id"])
    cur = read_active("vllm_config", apps.store.root)
    assert cur["proposal_id"] == p["id"] and cur["config"]["quantization"] == "fp8"
    assert "--quantization" in cur["vllm_args"]
    apps.rollback(p["id"], "user:a")
    assert read_active("vllm_config", apps.store.root) is None


def test_second_apply_rollback_restores_the_previous_config(apps):
    def spec(q):
        return {"kind": m.CLOUD_POLICY, "title": f"policy {q}", "summary": "effort per class",
                "change": {"target": "x/y", "before": {"code": "xhigh"}, "after": {"code": q}},
                "savings": [{"metric": "cost", "value": 20, "unit": "%", "label": "ESTIMATE"}]}

    a, b = apps.propose(spec("low")), apps.propose(spec("medium"))
    for p in (a, b):
        apps.approve(p["id"], "user:a")
        apps.apply(p["id"])
    assert read_active("cloud_policy", apps.store.root)["config"]["code"] == "medium"
    apps.rollback(b["id"], "user:a")
    assert read_active("cloud_policy", apps.store.root)["config"]["code"] == "low"


# ---- proposers -----------------------------------------------------------------------------

def test_checks_from_gates_maps_quality_p99_and_noise():
    out = checks_from_gates([
        GateResult("fixed", True, "pass rate held"),
        GateResult("p99", False, "too slow", {"baseline_p99_s": 1.0, "candidate_p99_s": 1.3,
                                              "budget_s": 1.1}),
        GateResult("noise", True, "saving is above the noise")])
    assert out["quality"]["passed"] is True
    assert out["p99"]["passed"] is False and out["p99"]["increase_pct"] == 30.0
    assert out["noise"]["passed"] is True


def test_flex_spec_is_simulated_and_validates(apps):
    ev = {"id": "e1", "kind": "shed", "target_pct": 25.0, "duration_s": 120, "note": ""}
    spec = flex_shed_spec(ev, estimate={"delivered_pct": 26.0, "quality_ok": True,
                                        "p99_increase_pct": 8.0})
    p = apps.propose(spec)
    assert p["label"] == "SIMULATED" and p["kind"] == m.FLEX_SHED


def test_cloud_spec_keeps_the_receipts_label():
    r = {"receipt_id": "cloud_1", "label": "MEASURED", "labels": {"usd": "ESTIMATE"},
         "reference_effort": "xhigh", "totals": {"saving_pct": 31.0},
         "classes": [{"class": "code", "chosen_effort": "low", "quality_drop_pts": 0.2,
                      "p99_increase_pct": 3.0, "gates_passed": True}]}
    spec = from_cloud_receipt(copy.deepcopy(r))
    assert spec["kind"] == m.CLOUD_POLICY
    assert {s["label"] for s in spec["savings"]} <= {"ESTIMATE", "MEASURED", "SIMULATED", "MOCK"}
    assert spec["change"]["after"]["effort_by_class"] == {"code": "low"}


def test_a_policy_cannot_list_trial_grants_and_evaluate_refuses_them():
    with pytest.raises(pol.PolicyError, match="trial_grant"):
        pol.Policy.from_dict({"enabled": True, "kinds": {
            "trial_grant": {"min_saving_pct": 0, "max_p99_increase_pct": 100}}})
    on = pol.Policy.from_dict(POLICY)
    ok, why = on.evaluate({"kind": m.TRIAL_GRANT, "scope": m.TRIAL, "savings": [], "checks": {}})
    assert not ok and "never auto-approved" in why[0]
