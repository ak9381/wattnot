"""Approve-before-apply on an unattended run: an operator-set policy, recorded by name and time.

The change is filed PENDING, approved only if the policy's thresholds hold (the audit trail,
the receipt block and the UI all say "approved by policy, set by operator <who> at <when>"),
and applied only after that. Nothing here is labelled as a person's click.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from wattnot.approvals import CallbackApplier, model as m
from wattnot.approvals import policy as pol
from wattnot.approvals.guard import PENDING_POLICY, Guard
from wattnot.approvals.risk import RiskConfig
from wattnot.approvals.service import Approvals

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    t = 1_800_000_000.0

    def __call__(self):
        return self.t


def policy_dict(**over):
    d = {"format": pol.FORMAT, "enabled": True, "allowed_labels": ["MEASURED"],
         "kinds": {"power_cap": {"min_saving_pct": 3, "max_p99_increase_pct": 10,
                                 "max_cap_drop_pct": 45}},
         "max_per_day": 3, "set_by": "Akash K Raju", "set_at": "2026-10-30T14:00:00Z"}
    d.update(over)
    return d


def spec(after=400, before=700, label="MEASURED", p99=4.0):
    return {"kind": m.POWER_CAP, "title": f"Cap GPU 0 at {after} W",
            "summary": f"Lower the power cap from {before} W to {after} W.",
            "change": {"gpu": 0, "gpus": [0], "before": {"power_cap_w": before},
                       "after": {"power_cap_w": after},
                       "drop_pct": round((before - after) / before * 100, 2)},
            "savings": [{"metric": "energy per request", "value": 20.0, "unit": "%",
                         "label": label}],
            "checks": {"quality": {"passed": True}, "p99": {"passed": True, "increase_pct": p99}},
            "receipt_preview": {"evidence": {"label": label, "rounds": 3}}}


def guard_with(tmp_path, d):
    root = tmp_path / "approvals"
    root.mkdir()
    (root / "policy.json").write_text(json.dumps(d))
    clock = Clock()
    return Guard(Approvals(root, clock=clock), RiskConfig(), clock=clock)


def test_pending_then_policy_approved_then_applied(tmp_path):
    g = guard_with(tmp_path, policy_dict())
    out = g.submit(spec(), gates_passed=True, stock_cap_w=700, tdp_w=700)
    pid = out["proposal"]["id"]
    assert out["route"] == PENDING_POLICY and out["proposal"]["status"] == "pending"
    p = g.decide_by_policy(pid)
    assert p["status"] == "approved"
    note = p["decision"]["note"]
    assert note.startswith("approved by policy, set by operator Akash K Raju at "
                           "2026-10-30T14:00:00Z")
    assert p["decision"]["policy_set_by"] == "Akash K Raju"
    assert not p["decision"]["by"].startswith(("auto-approve-flag", "person"))
    g.apply(pid, applier=CallbackApplier(apply=lambda q: {"after_w": 400},
                                         rollback=lambda t: None))
    assert g.approvals.get(pid)["status"] == "applied"
    blk = g.receipt_block()
    assert blk["approval_policy"]["set_by"] == "Akash K Raju"
    assert blk["approval_policy"]["sha256"] == pol.Policy.from_dict(policy_dict()).sha256()
    ops = [(o["op"], o["by"]) for o in blk["ops"]]
    assert ops[0][0] == "approve" and ops[0][1].startswith("auto-policy:")
    assert ops[-1][0] == "apply"
    assert "approved by policy, set by operator" in blk["ops"][0]["note"]


def test_policy_does_not_approve_a_change_outside_its_limits(tmp_path):
    g = guard_with(tmp_path, policy_dict())
    out = g.submit(spec(after=300), gates_passed=True, stock_cap_w=700, tdp_w=700)  # 57% drop
    assert g.decide_by_policy(out["proposal"]["id"])["status"] == "pending"


@pytest.mark.parametrize("label", ["SIMULATED", "MOCK"])
def test_policy_never_approves_unmeasured_numbers(tmp_path, label):
    g = guard_with(tmp_path, policy_dict())
    out = g.submit(spec(label=label), gates_passed=True, stock_cap_w=700, tdp_w=700)
    assert g.decide_by_policy(out["proposal"]["id"])["status"] == "pending"


def test_orchestrate_refuses_a_policy_without_operator(tmp_path):
    from wattnot.proof.orchestrate import install_policy
    f = tmp_path / "p.json"
    d = policy_dict()
    d.pop("set_by")
    f.write_text(json.dumps(d))
    with pytest.raises(SystemExit):
        install_policy(f, tmp_path / "a")
    f.write_text(json.dumps(policy_dict()))
    install_policy(f, tmp_path / "a")
    assert json.loads((tmp_path / "a" / "policy.json").read_text())["set_by"] == "Akash K Raju"


def test_cli_policy_set_records_who_and_when(tmp_path):
    f = tmp_path / "approval-policy.json"
    env = {"PYTHONPATH": str(ROOT / "src")}
    r = subprocess.run([sys.executable, "-m", "wattnot.cli", "proposals", "--dir",
                        str(tmp_path / "d"), "policy", "set", "--file", str(f)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 2 and not f.exists()          # --by is required
    r = subprocess.run([sys.executable, "-m", "wattnot.cli", "proposals", "--dir",
                        str(tmp_path / "d"), "policy", "set", "--by", "Akash K Raju",
                        "--file", str(f)], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    p = pol.Policy.from_dict(json.loads(f.read_text()))
    assert p.enabled and p.set_by == "Akash K Raju" and p.set_at.endswith("Z")
    from wattnot.longrun.driver import approval_policy_problem
    assert approval_policy_problem(f) is None
    assert approval_policy_problem(tmp_path / "missing.json")


def test_live_sweep_uses_the_policy_not_the_flag(tmp_path):
    from wattnot.longrun import nomock
    from wattnot.longrun.driver import APPROVAL_POLICY, plan_glimmer_test
    sweep = next(b for b in plan_glimmer_test(tmp_path) if b.id == "t1-sweep")
    assert "--auto-approve" not in sweep.live
    assert sweep.live[sweep.live.index("--approval-policy") + 1] == APPROVAL_POLICY
    bad = ["python", "-m", "wattnot.proof.orchestrate", "--mode", "live", "--sam", "live",
           "--auto-approve"]
    assert any("--auto-approve" in h for h in nomock._banned_tokens(bad))
