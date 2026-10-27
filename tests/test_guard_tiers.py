"""Autonomy tiers: strict planners need a person for high-risk changes."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from wattnot.approvals import Approvals, CallbackApplier
from wattnot.approvals import model as m
from wattnot.approvals.appliers import NvidiaSmiApplier
from wattnot.approvals.guard import PENDING_STRICT, STRICT_REASON, Guard
from wattnot.approvals.risk import RiskConfig, tier_for
from wattnot.dash.server import create_app

OPEN = {"name": "muse-spark-open", "model": "meta-models/muse-spark-open-1026",
        "partner": "baseten"}
META = {"name": "muse-spark", "model": "muse-spark-1.3", "partner": "meta"}


def cap_spec(before, after):
    return {"kind": m.POWER_CAP, "title": f"Cap GPU 0 at {after} W",
            "summary": f"Lower the power cap from {before} W to {after} W.",
            "change": {"target": "gpu:0", "gpu": 0,
                       "before": {"power_cap_w": before}, "after": {"power_cap_w": after}},
            "savings": [{"metric": "energy", "value": 9.0, "unit": "%", "label": "MEASURED"}],
            "checks": {"quality": {"passed": True}, "p99": {"passed": True}}}


def guard(tmp_path, **kw):
    apps = Approvals(tmp_path, appliers={"power_cap": CallbackApplier(
        lambda p: {"gpu": 0, "before_w": 700, "after_w": 400}, lambda t: None)})
    return Guard(apps, RiskConfig(), **kw)


def test_tier_defaults_and_unknown_is_strict():
    assert tier_for(RiskConfig(), "muse-spark") == "standard"
    assert tier_for(RiskConfig(), "bisect") == "standard"
    assert tier_for(RiskConfig(), "muse-spark-open") == "strict"
    assert tier_for(RiskConfig(), "something-new") == "strict"
    assert tier_for(None, "muse-spark-open") == "strict"


def test_tiers_round_trip_and_reject_junk(tmp_path):
    cfg = RiskConfig.from_dict({"format": "wattnot.guardrail/1",
                                "tiers": {"muse-spark-open": "strict", "x": "standard"}})
    assert RiskConfig.from_dict(cfg.to_dict()) == cfg
    assert RiskConfig.from_dict({"format": "wattnot.guardrail/1"}).tiers["muse-spark"] \
        == "standard"
    for bad in ({"muse-spark": "lax"}, {"muse-spark": 1}, ["strict"]):
        with pytest.raises(ValueError):
            RiskConfig.from_dict({"format": "wattnot.guardrail/1", "tiers": bad})


def test_strict_low_risk_still_auto_applies(tmp_path):
    g = guard(tmp_path)
    out = g.submit(cap_spec(700, 650), gates_passed=True, planner=OPEN)
    assert out["route"] == "auto-low-risk"
    assert out["proposal"]["status"] == "approved"


def test_strict_high_risk_stays_pending_despite_the_flag(tmp_path):
    g = guard(tmp_path, auto_approve_by="orchestrate")
    out = g.submit(cap_spec(700, 400), gates_passed=True, planner=OPEN)
    assert out["route"] == PENDING_STRICT == "pending-strict"
    assert out["proposal"]["status"] == "pending"
    assert STRICT_REASON in out["risk"].reasons
    assert STRICT_REASON in out["proposal"]["change"]["risk"]["reasons"]
    src = out["proposal"]["source"]["planner"]
    assert src == {**OPEN, "tier": "strict"}
    assert out["proposal"]["content_sha256"] == m.content_hash(out["proposal"])
    notes = [e["detail"].get("note", "") for e in g.approvals.audit()
             if e["event"] == "proposed"]
    assert len(notes) == 1 and "pending-strict" in notes[0]
    assert "--auto-approve does not apply" in notes[0]


def test_standard_high_risk_with_flag_still_auto_approves(tmp_path):
    g = guard(tmp_path, auto_approve_by="orchestrate")
    out = g.submit(cap_spec(700, 400), gates_passed=True, planner=META)
    assert out["route"] == "auto-approve-flag"
    assert out["proposal"]["status"] == "approved"


def test_unknown_planner_name_is_strict(tmp_path):
    g = guard(tmp_path, auto_approve_by="orchestrate")
    out = g.submit(cap_spec(700, 400), gates_passed=True,
                   planner={"name": "mystery", "model": "m", "partner": None})
    assert out["route"] == "pending-strict"
    assert out["proposal"]["source"]["planner"]["tier"] == "strict"


def test_ops_rows_carry_the_planner(tmp_path):
    g = guard(tmp_path)
    low = g.submit(cap_spec(700, 650), gates_passed=True, planner=OPEN)["proposal"]["id"]
    ops = [o for o in g.ops() if o["change_id"] == low]
    assert ops and all(o["planner"] == {**OPEN, "tier": "strict"} for o in ops)
    assert g.receipt_block([low])["ops"][0]["planner"]["tier"] == "strict"


def test_approve_reject_partner_operator_or_policy(tmp_path):
    g = guard(tmp_path)
    low = g.submit(cap_spec(700, 650), gates_passed=True)["proposal"]["id"]
    high = g.submit(cap_spec(700, 400), gates_passed=True)["proposal"]["id"]
    g.approvals.reject(high, "user:ana", note="no")
    ops = {o["change_id"]: o for o in g.ops()}
    assert ops[low]["partner"] == {"id": "policy", "name": "Auto policy"}
    assert ops[high]["partner"] == {"id": "operator", "name": "Operator"}


def test_timeout_reject_is_policy(tmp_path):
    apps = Approvals(tmp_path)
    g = Guard(apps, RiskConfig())
    pid = g.submit(cap_spec(700, 400), gates_passed=True)["proposal"]["id"]
    g.wait(pid, timeout_s=0, sleep=lambda s: None)
    (rej,) = [o for o in g.ops() if o["change_id"] == pid]
    assert (rej["op"], rej["by"]) == ("reject", "system:timeout")
    assert rej["partner"] == {"id": "policy", "name": "Auto policy"}


def _smi(stock=700):
    state = {"cap": stock}

    def run(cmd, **kw):
        if "-pl" in cmd:
            state["cap"] = int(cmd[cmd.index("-pl") + 1])
        return SimpleNamespace(stdout=f"{state['cap']}\n")
    return NvidiaSmiApplier(run=run)


def test_apply_undo_partner_on_a_real_card_and_null_on_mock(tmp_path):
    apps = Approvals(tmp_path, appliers={"power_cap": _smi()})
    g = Guard(apps, RiskConfig())
    pid = g.submit(cap_spec(700, 650), gates_passed=True)["proposal"]["id"]
    g.apply(pid)
    g.undo(pid, "user:ana")
    ops = [o for o in g.ops() if o["change_id"] == pid]
    assert [o["partner"] for o in ops if o["op"] in ("apply", "undo")] == [
        {"id": "e2e", "name": "E2E H100"}, {"id": "e2e", "name": "E2E H100"}]
    mock_apps = Approvals(tmp_path / "mock", appliers={"power_cap": _smi()})
    mock_g = Guard(mock_apps, RiskConfig())
    spec = cap_spec(700, 650)
    spec["savings"] = [{"metric": "energy", "value": 9.0, "unit": "%", "label": "MOCK"}]
    pid = mock_g.submit(spec, gates_passed=True)["proposal"]["id"]
    mock_g.apply(pid)
    (op,) = [o for o in mock_g.ops() if o["op"] == "apply"]
    assert op["partner"] is None


def test_proposed_by_from_the_planner(tmp_path):
    g = guard(tmp_path)
    meta_pid = g.submit(cap_spec(700, 650), gates_passed=True,
                        planner=META)["proposal"]["id"]
    open_pid = g.submit(cap_spec(700, 650), gates_passed=True,
                        planner=OPEN)["proposal"]["id"]
    ops = {o["change_id"]: o for o in g.ops()}
    assert ops[meta_pid]["proposed_by"] == {"id": "meta", "name": "Meta"}
    assert ops[open_pid]["proposed_by"] == {"id": "baseten", "name": "Baseten"}


def test_dashboard_item_carries_the_planner(tmp_path):
    g = Guard(Approvals(tmp_path), RiskConfig())
    g.submit(cap_spec(700, 400), gates_passed=True, planner=OPEN)
    g.submit(cap_spec(700, 500), gates_passed=True)
    c = TestClient(create_app(approvals_dir=tmp_path))
    pending = {p["change"]["after"]["power_cap_w"]: p
               for p in c.get("/api/dash/approvals").json()["pending"]}
    assert len(pending) == 2
    planners = [p["planner"] for p in pending.values()]
    assert {**OPEN, "tier": "strict"} in planners
    assert None in planners
    assert json.loads(c.get("/api/dash/approvals").text)["pending"][0]["planner"] \
        in ({**OPEN, "tier": "strict"}, None)
