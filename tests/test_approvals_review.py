"""The shared approve/reject/HOLD rule: a skipped check holds, it never silently approves/rejects."""

from __future__ import annotations

from wattnot.approvals.review import held_decision


def test_all_pass_is_approved():
    r = held_decision({"quality": True, "p99": True})
    assert r["decision"] == "approved" and not r["failed_checks"] and not r["skipped_checks"]


def test_skipped_check_holds():
    r = held_decision({"quality": True, "spark_agreement": None})
    assert r["decision"] == "held" and r["skipped_checks"] == ["spark_agreement"]
    assert not r["failed_checks"]


def test_real_failure_rejects():
    r = held_decision({"quality": False, "p99": True})
    assert r["decision"] == "rejected" and r["failed_checks"] == ["quality"]


def test_failure_dominates_a_skip():
    # a real failure rejects even when another check is only skipped
    r = held_decision({"quality": False, "spark_agreement": None})
    assert r["decision"] == "rejected"
    assert r["failed_checks"] == ["quality"] and r["skipped_checks"] == ["spark_agreement"]


def test_accepts_modal_verify_change_shape():
    # {name: {"ok": True/False/None, ...}} — the Modal runner's checks dict
    checks = {"accuracy": {"ok": True}, "spark_agreement": {"ok": None, "skipped": "off"}}
    assert held_decision(checks)["decision"] == "held"


def test_accepts_list_of_check_dicts():
    checks = [{"check": "a", "ok": True}, {"check": "b", "ok": None}]
    r = held_decision(checks)
    assert r["decision"] == "held" and r["skipped_checks"] == ["b"]
