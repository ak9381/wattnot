"""Approve before apply: CLI, local HTTP API, and the places that used to apply on their own.

Fakes only (fake clock, FakeNVML, the simulator rig, the bundled demo transport).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from fixtures.nvml_fake import FakeNVML
from wattnot import runbook as R
from wattnot.approvals import ApprovalGate, Approvals, NotApproved
from wattnot.approvals import model as m
from wattnot.approvals.server import create_app
from wattnot.cli import main as wattnot
from wattnot.control.nvml_ctl import CapGuard
from wattnot.flex import cli as flex_cli
from wattnot.flex import signal as sig
from wattnot.flex.hw import run_on_gpu
from wattnot.flex.run import SimBackend, run_flex
from wattnot.runner.vllm_server import TrialParams

ROOT = Path(__file__).resolve().parents[1]
QUICK = R.Plan(caps=(700, 600, 500), reps=5, repeat=1, concurrency=1, ablation_trials=4)
SIM = {"kind": m.VLLM_CONFIG, "title": "vLLM fp8", "summary": "Restart vLLM with fp8.",
       "change": {"target": "vllm", "before": {"quantization": "none", "max_num_seqs": 256},
                  "after": {"quantization": "fp8", "max_num_seqs": 512},
                  "changed": ["quantization", "max_num_seqs"]},
       "savings": [{"metric": "energy per request", "value": 15.0, "unit": "%",
                    "label": "SIMULATED"}],
       "checks": {"quality": {"passed": True}, "p99": {"passed": True, "increase_pct": -3.0}}}


def make(tmp_path):
    return Approvals(tmp_path / "p")


# ---- CLI -----------------------------------------------------------------------------------

def test_cli_list_show_approve_apply_rollback(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("WATTNOT_APPROVER", "akash")
    d = str(tmp_path / "p")
    pid = make(tmp_path).propose(SIM)["id"]
    assert wattnot(["proposals", "--dir", d, "list"]) == 0
    assert pid in capsys.readouterr().out
    assert wattnot(["proposals", "--dir", d, "show", pid[:20]]) == 0
    shown = capsys.readouterr().out
    for want in ("PENDING", "quantization: none -> fp8", "[SIMULATED]", "Checks:",
                 "Receipt preview"):
        assert want in shown
    assert wattnot(["proposals", "--dir", d, "apply", pid]) == 2  # not approved yet
    assert not (tmp_path / "p" / "active").exists()
    assert wattnot(["proposals", "--dir", d, "approve", pid, "--note", "ok"]) == 0
    assert "nothing is applied yet" in capsys.readouterr().out
    assert not (tmp_path / "p" / "active").exists()
    assert wattnot(["proposals", "--dir", d, "apply", pid]) == 0
    assert (tmp_path / "p" / "active" / "vllm_config.json").exists()
    assert wattnot(["proposals", "--dir", d, "rollback", pid]) == 0
    assert not (tmp_path / "p" / "active" / "vllm_config.json").exists()
    assert wattnot(["proposals", "--dir", d, "verify"]) == 0
    assert wattnot(["proposals", "--dir", d, "audit", pid]) == 0
    out = capsys.readouterr().out
    assert "user:akash" in out and "rolled_back" in out


def test_cli_reject_and_json_and_prefix_errors(tmp_path, capsys):
    d = str(tmp_path / "p")
    a = make(tmp_path)
    p1, p2 = a.propose(SIM)["id"], a.propose(SIM)["id"]
    assert wattnot(["proposals", "--dir", d, "reject", p1, "--note", "no"]) == 0
    assert wattnot(["proposals", "--dir", d, "approve", p1]) == 2
    assert wattnot(["proposals", "--dir", d, "show", "prop_nope"]) == 2
    assert wattnot(["proposals", "--dir", d, "show", "prop_"]) == 2  # ambiguous prefix
    capsys.readouterr()
    assert wattnot(["proposals", "--dir", d, "list", "--status", "pending", "--json"]) == 0
    assert [x["id"] for x in json.loads(capsys.readouterr().out)] == [p2]


def test_cli_approve_with_a_stale_hash_is_refused(tmp_path):
    d = str(tmp_path / "p")
    pid = make(tmp_path).propose(SIM)["id"]
    assert wattnot(["proposals", "--dir", d, "approve", pid, "--sha256", "0000"]) == 2
    assert Approvals(d).get(pid)["status"] == m.PENDING


def test_cli_policy_init_is_off_and_never_overwrites(tmp_path, capsys):
    d = str(tmp_path / "p")
    assert wattnot(["proposals", "--dir", d, "policy", "init"]) == 0
    assert json.loads((tmp_path / "p" / "policy.json").read_text())["enabled"] is False
    assert wattnot(["proposals", "--dir", d, "policy", "init"]) == 1
    assert wattnot(["proposals", "--dir", d, "policy", "show"]) == 0
    assert '"enabled": false' in capsys.readouterr().out


def test_cli_expire(tmp_path, capsys):
    d = str(tmp_path / "p")
    a = Approvals(d, clock=lambda: 1_000_000_000.0)
    a.propose({**SIM, "ttl_s": 10})
    assert wattnot(["proposals", "--dir", d, "expire"]) == 0
    assert "expired prop_" in capsys.readouterr().out


def test_cli_link_receipt(tmp_path, capsys):
    d = str(tmp_path / "p")
    a = make(tmp_path)
    pid = a.propose(SIM)["id"]
    a.approve(pid, "user:a")
    a.apply(pid)
    f = tmp_path / "r.json"
    f.write_text(json.dumps({"receipt_id": "rcpt_1", "integrity": {"sha256": "ef" * 32}}))
    assert wattnot(["proposals", "--dir", d, "link-receipt", pid, str(f)]) == 0
    assert a.get(pid)["receipt"]["sha256"] == "ef" * 32


def test_cli_apply_of_a_power_cap_needs_the_gpu_host(tmp_path, capsys):
    d = str(tmp_path / "p")
    a = make(tmp_path)
    pid = a.propose({"kind": m.POWER_CAP, "title": "cap", "summary": "s",
                     "change": {"gpu": 0, "before": {"power_cap_w": 700},
                                "after": {"power_cap_w": 500}},
                     "savings": [{"metric": "e", "value": 9, "unit": "%",
                                  "label": "MEASURED"}]})["id"]
    a.approve(pid, "user:a")
    assert wattnot(["proposals", "--dir", d, "apply", pid]) == 2  # no GPU here (and no NVML)
    assert a.get(pid)["status"] == m.APPROVED


# ---- HTTP API ------------------------------------------------------------------------------

@pytest.fixture
def api(tmp_path):
    a = make(tmp_path)
    return a, TestClient(create_app(a, token="t0ken"))


def auth(extra=None):
    return {"Authorization": "Bearer t0ken", **(extra or {})}


def test_api_reads_are_open_writes_need_the_token(api):
    a, c = api
    pid = a.propose(SIM)["id"]
    assert c.get("/v1/health").json()["pending"] == 1
    assert c.get("/v1/proposals").json()[0]["id"] == pid
    assert c.get(f"/v1/proposals/{pid}").json()["audit"][0]["event"] == "proposed"
    assert c.post(f"/v1/proposals/{pid}/approve", json={}).status_code == 401
    assert c.post(f"/v1/proposals/{pid}/approve", json={},
                  headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert a.get(pid)["status"] == m.PENDING


def test_api_approve_apply_rollback_audit(api):
    a, c = api
    p = a.propose(SIM)
    pid = p["id"]
    r = c.post(f"/v1/proposals/{pid}/approve", headers=auth(),
               json={"by": "akash", "note": "ok", "sha256": p["content_sha256"]})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert r.json()["decision"]["via"] == "http" and r.json()["decision"]["by"] == "akash"
    done = c.post(f"/v1/proposals/{pid}/apply", headers=auth(), json={}).json()
    assert done["status"] == "applied"
    assert c.post(f"/v1/proposals/{pid}/rollback", headers=auth(), json={}).json()[
        "status"] == "rolled_back"
    assert c.get("/v1/audit/verify").json() == {"ok": True, "entries": 4, "problems": []}
    assert [e["event"] for e in c.get("/v1/audit", params={"proposal_id": pid}).json()] == [
        "proposed", "approved", "applied", "rolled_back"]


def test_api_errors_are_json(api):
    a, c = api
    pid = a.propose(SIM)["id"]
    assert c.get("/v1/proposals/prop_missing").json()["code"] == "not_found"
    assert c.post(f"/v1/proposals/{pid}/apply", headers=auth(), json={}).status_code == 409
    bad = c.post(f"/v1/proposals/{pid}/approve", headers=auth(), json={"sha256": "00"})
    assert bad.status_code == 409 and bad.json()["code"] == "conflict"
    assert c.get("/v1/proposals", params={"status": "weird"}).status_code == 400
    assert a.get(pid)["status"] == m.PENDING


def test_api_policy_is_read_only_and_off_by_default(api):
    _, c = api
    body = c.get("/v1/policy").json()
    assert body["enabled"] is False and body["editable_here"] is False
    assert c.post("/v1/policy", headers=auth(), json={"enabled": True}).status_code in (404, 405)


def test_api_rejects_foreign_host_headers(api):
    _, c = api
    assert c.get("/v1/health", headers={"host": "evil.example"}).status_code == 400


def test_api_needs_a_token_to_be_built(tmp_path):
    with pytest.raises(ValueError):
        create_app(make(tmp_path), token="")


# ---- the hardware boundary -----------------------------------------------------------------

def test_capguard_permit_blocks_the_write_but_not_the_restore():
    nv = FakeNVML(power_limit_mw=700_000)
    allowed = []

    def permit(w):
        if w < 400:
            raise NotApproved("too low")
        allowed.append(w)

    with CapGuard(nv, nv.device_handle(0), handle_sigterm=False, permit=permit) as g:
        g.set_cap_w(500)
        with pytest.raises(NotApproved):
            g.set_cap_w(250)
        assert nv.current_limit_mw == 500_000
    assert nv.current_limit_mw == 700_000 and allowed == [500]


def test_flex_on_a_gpu_cannot_pass_the_permit_and_still_restores(tmp_path):
    prog = tmp_path / "p.jsonl"
    prog.write_text('{"p99_ms": 1000, "pass_rate": 0.8, "rps": 8}\n')
    nv = FakeNVML(power_mw=300_000, power_limit_mw=650_000)

    def deny(_w):
        raise NotApproved("revoked")

    with pytest.raises(NotApproved):
        run_on_gpu(nv, sig.ShedEvent("e", 20.0, 20), prog,
                   cfg=flex_cli.FlexConfig(pre_s=6, post_s=6), sleep=lambda s: None, permit=deny)
    assert nv.current_limit_mw == 650_000


def _flex_args(tmp_path, **kw):
    prog = tmp_path / "prog.jsonl"
    prog.write_text('{"p99_ms": 1000, "pass_rate": 0.8, "rps": 8}\n')
    base = dict(backend="gpu", progress=str(prog), gpu=0, nvml=None, seed=7, pre_s=6, post_s=6,
                sla_slack=0.5, created_at="2026-10-27T10:00:00Z", json=True, sign_key=None,
                out=str(tmp_path / "out"), proposals_dir=str(tmp_path / "p"),
                pre_approved=None, approval_wait_s=0.0)
    return argparse.Namespace(**{**base, **kw})


def _flex_go(a, nv):
    ev = sig.ShedEvent("hw-1", 20.0, 20)
    cfg = flex_cli.FlexConfig(pre_s=6, post_s=6)
    return flex_cli._run_events(a, sig.load(), [ev], cfg, nv)


def test_flex_gpu_shed_without_approval_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(flex_cli.time, "sleep", lambda s: None)
    nv = FakeNVML(power_mw=300_000, power_limit_mw=650_000)
    assert _flex_go(_flex_args(tmp_path), nv) == 50
    assert nv.power_history == [] and not (tmp_path / "out").exists()
    (p,) = Approvals(tmp_path / "p").list()
    assert p["kind"] == m.FLEX_SHED and p["status"] == m.REJECTED


def test_flex_gpu_shed_pre_approved_links_the_receipt_both_ways(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(flex_cli.time, "sleep", lambda s: None)
    nv = FakeNVML(power_mw=300_000, power_limit_mw=650_000)
    assert _flex_go(_flex_args(tmp_path, pre_approved="session-1"), nv) == 0
    assert nv.current_limit_mw == 650_000  # put back
    a = Approvals(tmp_path / "p")
    (p,) = a.list()
    assert p["status"] == m.APPLIED and p["decision"]["by"] == "pre-approved:session-1"
    (rf,) = [f for f in (tmp_path / "out").glob("rcpt_flex_*.json")
             if not f.name.endswith((".trace.json", ".sigstore.json"))]
    r = json.loads(rf.read_text())
    assert r["approval"]["proposal_sha256"] == p["content_sha256"]  # receipt -> proposal
    assert p["receipt"]["sha256"] == r["integrity"]["sha256"]       # proposal -> receipt
    assert a.verify() == []


def test_flex_on_the_simulator_needs_no_approval(tmp_path):
    assert wattnot(["flex", "run", "--out", str(tmp_path / "o")]) == 0
    assert not (tmp_path / "proposals").exists()


# ---- the H100 runbook ----------------------------------------------------------------------

class ModelStub:
    key, repo, tp, vllm_image, extra_args = "stub", "org/stub", 1, None, ()


def _gate(tmp_path, **kw):
    return ApprovalGate(Approvals(tmp_path / "p"), announce=lambda s: None, sleep=lambda s: None,
                        poll_s=0, **kw)


def _run(tmp_path, gate, **rig):
    return R.run(R.FakeRig(**rig), tmp_path / "h", plan=QUICK, stages=R.KIT_STAGES,
                 key_path=tmp_path / "k.pem", run_tag="20261030_090000", gate=gate)


def test_runbook_exits_50_when_nobody_approves_and_changes_nothing(tmp_path):
    rig = R.FakeRig()
    code = R.run(rig, tmp_path / "h", plan=QUICK, stages=R.KIT_STAGES, gate=_gate(
        tmp_path, mode="ask", wait_s=0), key_path=tmp_path / "k.pem", run_tag="t")
    assert code == R.EXIT_APPROVAL == 50
    assert rig.cap == 700 and not (tmp_path / "h" / "receipts").exists()


def test_runbook_pre_approved_runs_and_is_audited(tmp_path):
    code = _run(tmp_path, _gate(tmp_path, mode="pre_approved", name="oct30 (by akash)", wait_s=0))
    assert code == 0
    s = json.loads((tmp_path / "h" / "summary.json").read_text())
    ap = s["approvals"]
    assert ap["mode"] == "pre_approved" and ap["status"] == "closed"
    assert ap["approved_by"] == "pre-approved:oct30 (by akash)" and ap["audit_chain_problems"] == []
    assert (tmp_path / "h" / "approvals" / "audit.jsonl").exists()
    grant = Approvals(tmp_path / "p").get(ap["grant"])
    assert grant["scope"] == "trial" and grant["change"]["restores_on_exit"] is True


def test_runbook_files_pending_proposals_and_applies_none(tmp_path):
    assert _run(tmp_path, _gate(tmp_path, mode="pre_approved", name="x", wait_s=0)) == 0
    a = Approvals(tmp_path / "p")
    commits = [p for p in a.list() if p["scope"] == m.COMMIT]
    assert commits, "the best settings should be filed as proposals"
    assert {p["status"] for p in commits} == {m.PENDING}
    assert not (tmp_path / "p" / "active").exists()
    for p in commits:
        assert p["label"] == "SIMULATED" and p["checks"]["quality"]["passed"] is True
        assert p["receipt_preview"]["evidence_receipt"]["sha256"]


def test_runbook_fake_rig_without_a_gate_uses_a_dry_run_and_keeps_working(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _run(tmp_path, None) == 0
    s = json.loads((tmp_path / "h" / "summary.json").read_text())
    assert s["approvals"]["mode"] == "dry_run" and s["receipt_labels"] == {"SIMULATED": 5}
    assert not (tmp_path / "proposals").exists()  # a dry run leaves no store behind


def test_a_real_rig_with_no_gate_does_nothing(tmp_path):
    class Real(R.FakeRig):
        provider = "e2e"

    rig = Real()
    assert R.run(rig, tmp_path / "h", plan=QUICK, key_path=tmp_path / "k.pem", run_tag="t") == 50
    assert rig.cap == 700


def test_real_rig_refuses_changes_without_an_open_real_grant(tmp_path):
    rig = object.__new__(R.RealRig)  # no GPU needed: only the permission checks run
    rig.nvml, rig.handles, rig.gpus, rig.session = FakeNVML(power_limit_mw=700_000), [0], 1, None
    with pytest.raises(NotApproved):
        rig.set_cap(500)
    dry = _gate(tmp_path, mode="dry_run")
    with dry.session(title="sim", bounds={"power_cap_w": {"min": 400, "max": 700}}) as s:
        rig.session = s
        with pytest.raises(NotApproved, match="real GPU"):
            rig.set_cap(500)  # a dry-run grant is not a licence to touch a real card
    assert rig.nvml.power_history == []
    real = _gate(tmp_path, mode="pre_approved", name="t")
    with real.session(title="real", bounds={"power_cap_w": {"min": 400, "max": 700},
                                            "gpus": [0]}) as s:
        rig.session = s
        rig.set_cap(500)
        assert rig.nvml.current_limit_mw == 500_000
        with pytest.raises(NotApproved):
            rig.set_cap(300)
        assert rig.nvml.current_limit_mw == 500_000
        with pytest.raises(NotApproved):
            rig.model = ModelStub()
            rig.serving = None
            rig._serve(TrialParams())  # vLLM restarts are not in this grant


def test_runbook_main_flags(tmp_path):
    d = tmp_path / "pp"
    assert R.main(["--out", str(tmp_path / "o"), "--quick", "--stages", "setup,probe",
                   "--proposals-dir", str(d), "--pre-approved", "me"]) == 0
    (g,) = Approvals(d).list()
    assert g["decision"]["by"] == "pre-approved:me"


def test_kit_script_fake_run_keeps_exit_zero_and_keeps_its_proposals(tmp_path):
    if not shutil.which("bash"):
        return
    out = tmp_path / "oct30"
    r = subprocess.run(["bash", str(ROOT / "scripts" / "h100_oct30.sh"), "--fake", "--pre-approved",
                        "--approved-by", "akash", "--out", str(out)],
                       capture_output=True, text=True, timeout=300,
                       env={**os.environ, "HOME": str(tmp_path),
                            "PYTHON": sys.executable})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PRE-APPROVED by flag" in r.stdout
    assert "receipt labels: {'SIMULATED': 5}" in r.stdout
    audit = (out / "proposals" / "audit.jsonl").read_text()
    assert "pre-approved:h100_oct30.sh (by akash)" in audit


def test_kit_script_documents_the_flags():
    text = (ROOT / "scripts" / "h100_oct30.sh").read_text()
    for want in ("--pre-approved", "exit 50", "50 nobody approved", "--approval-wait-s"):
        assert want in text or want.replace("exit 50", "then exits 50") in text


# ---- cloud ---------------------------------------------------------------------------------

def test_cloud_tune_files_a_pending_policy_and_applies_nothing(tmp_path, capsys):
    d = tmp_path / "pp"
    assert wattnot(["cloud", "tune", "--demo", "--out", str(tmp_path / "c"),
                    "--proposals-dir", str(d)]) == 0
    (p,) = Approvals(d).list()
    assert p["kind"] == m.CLOUD_POLICY and p["status"] == m.PENDING
    assert p["label"] in ("SIMULATED", "ESTIMATE", "MOCK", "MEASURED")
    assert not (d / "active").exists()
    assert "nothing changes until a person approves it" in capsys.readouterr().out


def test_cloud_tune_no_propose(tmp_path):
    d = tmp_path / "pp"
    assert wattnot(["cloud", "tune", "--demo", "--out", str(tmp_path / "c"),
                    "--proposals-dir", str(d), "--no-propose"]) == 0
    assert not d.exists()


def test_headline_decision_is_exposed_for_proposals():
    from wattnot import headline as H

    assert "decision" in H.HeadlineResult.__dataclass_fields__


def test_sim_backend_still_runs_without_a_gate():
    r = run_flex(SimBackend(seed=7), sig.load().events[0], flex_cli.FlexConfig(pre_s=6, post_s=6))
    assert r.result["status"] in ("full", "partial", "none")
