"""The guardrail service: file, route, wait, apply, undo, and the receipt trail."""
from __future__ import annotations

import json

import pytest

from wattnot.approvals import Approvals, CallbackApplier, ConfigFileApplier, PowerCapApplier
from wattnot.approvals import model as m
from wattnot.approvals.appliers import ApplyError  # noqa: F401
from wattnot.approvals.gate import NotApproved
from wattnot.approvals.guard import Guard
from wattnot.approvals.risk import RiskConfig
from wattnot.approvals.service import StateError


class Clock:
    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock():
    return Clock()


caps: dict = {}


def cap_spec(before, after, **over):
    spec = {"kind": m.POWER_CAP, "title": f"Cap GPU 0 at {after} W",
            "summary": f"Lower the power cap from {before} W to {after} W.",
            "change": {"target": "gpu:0", "gpu": 0, "before": {"power_cap_w": before},
                       "after": {"power_cap_w": after}},
            "savings": [{"metric": "energy", "value": 9.0, "unit": "%", "label": "MEASURED"}],
            "checks": {"quality": {"passed": True}, "p99": {"passed": True}}}
    spec.update(over)
    return spec


def vllm_spec(after, before=None):
    change = {"after": dict(after)}
    if before is not None:
        change["before"] = dict(before)
    return {"kind": m.VLLM_CONFIG, "title": "Tune vLLM",
            "summary": "Change the batch size.",
            "change": change,
            "savings": [{"metric": "energy", "value": 5.0, "unit": "%", "label": "MEASURED"}],
            "checks": {"quality": {"passed": True}, "p99": {"passed": True}}}


def test_low_risk_auto_approves_and_applies(tmp_path, clock):
    caps.clear()
    caps[0] = 700
    apps = Approvals(tmp_path / "p", clock=clock,
                     appliers={"power_cap": PowerCapApplier(
                         lambda i: caps[i], lambda i, w: (caps.__setitem__(i, w), w)[1])})
    g = Guard(apps, RiskConfig(), clock=clock)
    out = g.submit(cap_spec(700, 650), gates_passed=True)
    assert out["route"] == "auto-low-risk"
    assert out["proposal"]["status"] == "approved"
    assert out["proposal"]["decision"]["by"] == "guardrail:low-risk"
    p = g.apply(out["proposal"]["id"])
    assert p["status"] == "applied" and caps[0] == 650


def test_high_risk_stays_pending(tmp_path, clock):
    g = Guard(Approvals(tmp_path / "p", clock=clock), RiskConfig(), clock=clock)
    out = g.submit(cap_spec(700, 400), gates_passed=True)
    assert out["route"] == "pending"
    assert out["proposal"]["status"] == "pending"
    assert out["risk"].level == "high"


def test_approve_then_apply(tmp_path, clock):
    g = Guard(Approvals(tmp_path / "p", clock=clock,
                        appliers={"power_cap": CallbackApplier(
                            lambda p: {"gpu": 0, "before_w": 700, "after_w": 400},
                            lambda t: None)}), RiskConfig(), clock=clock)
    pid = g.submit(cap_spec(700, 400), gates_passed=True)["proposal"]["id"]
    g.approvals.approve(pid, "user:ana")
    assert g.apply(pid)["status"] == "applied"


def test_reject(tmp_path, clock):
    g = Guard(Approvals(tmp_path / "p", clock=clock), RiskConfig(), clock=clock)
    pid = g.submit(cap_spec(700, 400), gates_passed=True)["proposal"]["id"]
    g.approvals.reject(pid, "user:ana", note="too deep")
    with pytest.raises(StateError):
        g.apply(pid)


def test_timeout_is_reject(tmp_path, clock):
    g = Guard(Approvals(tmp_path / "p", clock=clock), RiskConfig(), clock=clock)
    pid = g.submit(cap_spec(700, 400), gates_passed=True)["proposal"]["id"]
    clock.t += 601  # past the 600 s guardrail ttl
    assert g.wait(pid, sleep=lambda s: None) == "expired"
    with pytest.raises(StateError):
        g.apply(pid)


def test_wait_short_timeout_expires_a_pending_card(tmp_path, clock):
    g = Guard(Approvals(tmp_path / "p", clock=clock), RiskConfig(), clock=clock)
    pid = g.submit(cap_spec(700, 400), gates_passed=True)["proposal"]["id"]
    assert g.wait(pid, timeout_s=0, sleep=lambda s: None) == "expired"


def test_wait_returns_a_decision(tmp_path, clock):
    g = Guard(Approvals(tmp_path / "p", clock=clock), RiskConfig(), clock=clock)
    pid = g.submit(cap_spec(700, 400), gates_passed=True)["proposal"]["id"]
    g.approvals.approve(pid, "user:ana")
    assert g.wait(pid, sleep=lambda s: None) == "approved"


def test_auto_approve_flag_is_unmistakable(tmp_path, clock):
    g = Guard(Approvals(tmp_path / "p", clock=clock), RiskConfig(),
              auto_approve_by="rehearsal", clock=clock)
    out = g.submit(cap_spec(700, 400), gates_passed=True)
    assert out["route"] == "auto-approve-flag"
    ev = [e for e in g.approvals.audit() if e["event"] == "approved"][0]
    assert ev["actor"] == "auto-approve-flag:rehearsal"
    assert ev["via"] == "pre-approved"
    assert "not a person's decision" in g.approvals.get(out["proposal"]["id"])["decision"]["note"]


def test_failed_gates_store_nothing(tmp_path, clock):
    g = Guard(Approvals(tmp_path / "p", clock=clock), RiskConfig(), clock=clock)
    with pytest.raises(NotApproved):
        g.submit(cap_spec(700, 650), gates_passed=False)
    assert g.approvals.list() == [] and g.approvals.audit() == []


def test_risk_is_part_of_the_hashed_content(tmp_path, clock):
    g = Guard(Approvals(tmp_path / "p", clock=clock), RiskConfig(), clock=clock)
    p = g.submit(cap_spec(700, 650), gates_passed=True)["proposal"]
    assert p["change"]["risk"]["level"] == "low"
    assert p["content_sha256"] == m.content_hash(p)


def test_undo_restores_the_power_cap(tmp_path, clock):
    caps.clear()
    caps[0] = 700
    apps = Approvals(tmp_path / "p", clock=clock,
                     appliers={"power_cap": PowerCapApplier(
                         lambda i: caps[i], lambda i, w: (caps.__setitem__(i, w), w)[1])})
    g = Guard(apps, RiskConfig(), clock=clock)
    pid = g.submit(cap_spec(700, 650), gates_passed=True)["proposal"]["id"]
    g.apply(pid)
    assert caps[0] == 650
    g.undo(pid, "user:ana")
    assert caps[0] == 700
    assert g.approvals.get(pid)["status"] == "rolled_back"


def test_undo_restores_the_vllm_config_file(tmp_path, clock):
    apps = Approvals(tmp_path / "p", clock=clock)
    before = {"format": "wattnot.active-vllm_config/1", "config": {"max_num_seqs": 32}}
    (tmp_path / "p" / "active").mkdir(parents=True, exist_ok=True)
    (tmp_path / "p" / "active" / "vllm_config.json").write_text(json.dumps(before))
    applier = ConfigFileApplier(apps.store, m.VLLM_CONFIG)
    apps.appliers[m.VLLM_CONFIG] = applier
    g = Guard(apps, RiskConfig(), clock=clock)
    pid = g.submit(vllm_spec({"max_num_seqs": 64}, {"max_num_seqs": 32}),
                   gates_passed=True)["proposal"]["id"]
    g.apply(pid)
    assert json.loads(applier.path.read_text())["config"] == {"max_num_seqs": 64}
    g.undo(pid, "user:ana")
    assert json.loads(applier.path.read_text()) == before


def test_ops_order_and_fields(tmp_path, clock):
    apps = Approvals(tmp_path / "p", clock=clock,
                     appliers={"power_cap": CallbackApplier(lambda p: {"t": 1}, lambda t: None)})
    g = Guard(apps, RiskConfig(), clock=clock)
    low = g.submit(cap_spec(700, 650), gates_passed=True)["proposal"]["id"]
    high = g.submit(cap_spec(700, 400), gates_passed=True)["proposal"]["id"]
    g.apply(low)
    g.approvals.reject(high, "user:ana", note="no")
    g.undo(low, "user:ana")
    ops = g.ops()
    assert [(o["op"], o["change_id"]) for o in ops] == [
        ("approve", low), ("apply", low), ("reject", high), ("undo", low)]
    assert ops[0]["risk"] == "low" and ops[2]["risk"] == "high"
    assert ops[0]["by"] == "guardrail:low-risk"
    assert set(ops[0]) == {"op", "change_id", "by", "via", "at", "note", "risk"}


def test_timeout_shows_as_reject_by_system(tmp_path, clock):
    g = Guard(Approvals(tmp_path / "p", clock=clock), RiskConfig(), clock=clock)
    pid = g.submit(cap_spec(700, 400), gates_passed=True)["proposal"]["id"]
    clock.t += 601
    g.wait(pid, sleep=lambda s: None)
    rej = [o for o in g.ops() if o["change_id"] == pid]
    assert [(o["op"], o["by"]) for o in rej] == [("reject", "system:timeout")]


def test_write_addendum_after_undo(tmp_path, clock):
    from wattnot.receipt.canon import canonical_sha256
    from wattnot.receipt.sign import generate_test_key

    apps = Approvals(tmp_path / "p", clock=clock,
                     appliers={"power_cap": CallbackApplier(
                         lambda p: {"gpu": 0, "before_w": 700, "after_w": 650},
                         lambda t: None)})
    g = Guard(apps, RiskConfig(), clock=clock)
    pid = g.submit(cap_spec(700, 650), gates_passed=True)["proposal"]["id"]
    g.apply(pid)
    g.undo(pid, "user:ana")
    unsigned = g.write_addendum(pid, tmp_path / "out")
    doc = json.loads(unsigned.read_text())
    assert doc["spec"] == "wattnot.guardrail-addendum/1"
    assert [o["op"] for o in doc["ops"]] == ["undo"]
    assert doc["ops"][0]["by"] == "user:ana"
    assert doc["integrity"]["sha256"] == canonical_sha256(doc)
    assert doc["integrity"]["signer"] is None  # no key: unsigned
    key = tmp_path / "key.pem"
    generate_test_key(key)
    signed = g.write_addendum(pid, tmp_path / "out2", sign_key=key)
    assert json.loads(signed.read_text())["integrity"]["signer"].startswith("offline:")


def test_receipt_block(tmp_path, clock):
    g = Guard(Approvals(tmp_path / "p", clock=clock), RiskConfig(),
              auto_approve_by="rehearsal", clock=clock)
    pid = g.submit(cap_spec(700, 400), gates_passed=True)["proposal"]["id"]
    block = g.receipt_block([pid])
    assert block["spec"] == "wattnot.guardrail-ops/1"
    assert block["auto_approve"] is True
    assert block["config"]["approval_timeout_s"] == 600
    assert block["audit_chain_ok"] is True
    assert len(block["audit_head_sha256"]) == 64
    assert [o["op"] for o in block["ops"]] == ["approve"]
