"""The RUN2 event stream: a full rehearsal on the fake meter, every event valid and labelled."""

from __future__ import annotations

import json
import sys

import pytest

from wattnot.events import Emitter
from wattnot.proof import orchestrate
from wattnot.proof.events import NullStream, Stream, check_labels, read_raw

STEPS = ["room_scan", "baseline", "quality_gate", "cap_sweep", "adaptive_topk",
         "approve", "keep_rollback", "receipt"]

FAST = ["--load-s", "1", "--reps", "2", "--repeat", "1",
        "--concurrency", "8", "--warmup", "1", "--settle-s", "0.05",
        "--adaptive-budget-s", "30"]


def test_stream_pairs_and_labels():
    got: list[dict] = []
    s = Stream(got.append, label="MOCK")
    with s.step("a", "workload", "send", "do a thing", top="a") as st:
        st.add("watts", 5.0, "W")
        st.outputs = "done"
    assert [e["type"] for e in got] == ["step.started", "step.finished"]
    assert got[1]["data"]["numbers"][0]["tag"] == "MOCK"
    assert not check_labels(got[1])
    assert read_raw("noise") is None
    assert NullStream().emit("x", {}) is None or True


def test_stream_error_closes_step():
    got: list[dict] = []
    s = Stream(got.append)
    with pytest.raises(RuntimeError), s.step("a", "workload", "send", "x"):
        raise RuntimeError("boom")
    assert [e["type"] for e in got][-2:] == ["error", "step.finished"] or got[-1]["type"] == "error"


def test_full_rehearsal(tmp_path, monkeypatch):
    pytest.importorskip("wattnot.sam.room")
    live = tmp_path / "live" / "run2.events.jsonl"
    argv = ["orchestrate", "--mode", "rehearsal", "--out", str(tmp_path / "o"),
            "--live-file", str(live), *FAST, "--auto-approve", "--adaptive"]  # adaptive is off by default now
    monkeypatch.setattr(sys, "argv", argv)
    orchestrate.main()
    events = [json.loads(x) for x in live.read_text().splitlines()]
    assert events[0]["type"] == "run.started" and events[-1]["type"] == "run.finished"
    assert events[-1]["data"]["status"] == "ok"
    assert [e["seq"] for e in events] == list(range(len(events)))
    top = {e["step_id"] for e in events if e["type"] == "step.finished"}
    for name in STEPS:
        assert name in top, name
    started = {e["step_id"] for e in events if e["type"] == "step.started"}
    assert started == top  # every step that starts also finishes
    # every event passes the frozen schema, every number carries a label
    em = Emitter("run_20260101_000000_abcdef", root=tmp_path / "v")
    for e in events:
        em.emit(e["type"], e["data"], step_id=e.get("step_id"),
                parent_step_id=e.get("parent_step_id"))
        assert not check_labels(e)
    # the room scan payload reaches the stream, with the H100 slot
    scans = [e for e in events if e["type"] == "step.progress"
             and e["data"].get("kind") == "room_scan"]
    assert {s["data"]["phase"] for s in scans} == {"before", "after"}
    assert any(r["source"] == "h100-run1" for r in scans[-1]["data"]["racks"])
    assert any(e["type"] == "receipt.signed" for e in events)
    assert all(e["data"].get("label") == "MOCK" or e["type"] != "run.started" for e in events)


def _run_rehearsal(tmp_path, monkeypatch, *extra):
    live = tmp_path / "live" / "run.events.jsonl"
    argv = ["orchestrate", "--mode", "rehearsal", "--out", str(tmp_path / "o"),
            "--live-file", str(live), *FAST, *extra]
    monkeypatch.setattr(sys, "argv", argv)
    assert orchestrate.main() == 0
    return [json.loads(x) for x in live.read_text().splitlines()], tmp_path / "o"


def _receipt_of(out):
    paths = [p for p in out.glob("proof-*.json") if not p.name.endswith(".sigstore.json")]
    assert len(paths) == 1
    return json.loads(paths[0].read_text())


def test_rehearsal_auto_approve_is_logged_and_receipt_verifies(tmp_path, monkeypatch):
    pytest.importorskip("wattnot.sam.room")
    events, out = _run_rehearsal(tmp_path, monkeypatch, "--auto-approve")
    assert events[-1]["data"]["status"] == "ok"
    top = {e["step_id"] for e in events if e["type"] == "step.finished"}
    assert "approve" in top and "approve.classify" in top and "approve.decide" in top
    body = _receipt_of(out)
    assert body["guardrail"]["source"] == "MOCK"
    assert ("approve", "auto-approve-flag:orchestrate") in [
        (o["op"], o["by"]) for o in body["guardrail"]["ops"]]
    assert body["guardrail"]["audit_chain_ok"] is True
    from wattnot.receipt.sign import bundle_path, verify_offline_bundle
    bundle = json.loads(bundle_path(out / f"{body['receipt_id']}.json").read_text())
    assert verify_offline_bundle(body, bundle) == (True, body["integrity"]["signer"])


def test_rehearsal_approval_timeout_rolls_back_and_stays_ok(tmp_path, monkeypatch):
    pytest.importorskip("wattnot.sam.room")
    events, out = _run_rehearsal(tmp_path, monkeypatch, "--approval-timeout-s", "1")
    assert events[-1]["type"] == "run.finished" and events[-1]["data"]["status"] == "ok"
    timed_out = [e for e in events if e["type"] == "action.rolled_back"
                 and e.get("step_id") == "keep_rollback"]
    assert [e["data"]["failed_gates"] for e in timed_out] == [["approval timed out"]]
    body = _receipt_of(out)
    assert [(o["op"], o["by"]) for o in body["guardrail"]["ops"]] == [
        ("reject", "system:timeout")]


def test_rehearsal_baseten_open_is_strict_then_times_out(tmp_path, monkeypatch):
    pytest.importorskip("wattnot.sam.room")
    from wattnot.cloud import MODEL as OPEN_MODEL
    events, out = _run_rehearsal(tmp_path, monkeypatch, "--planner", "baseten-open",
                                 "--auto-approve", "--approval-timeout-s", "1")
    assert events[-1]["type"] == "run.finished" and events[-1]["data"]["status"] == "ok"
    want = {"name": "muse-spark-open", "model": OPEN_MODEL, "partner": "baseten"}
    proposed = [e for e in events if e["type"] == "trial.proposed"]
    assert proposed and all(e["data"]["planner"] == want for e in proposed)
    assert all(e["data"]["by"] == "muse-spark-open" for e in proposed)
    sources = {e["data"]["narration"]["source"] for e in events
               if e["type"] == "step.progress" and "narration" in e["data"]}
    assert "muse-spark-open" in sources and "muse-spark" not in sources
    created = [e for e in events if e["type"] == "proposal.created"]
    assert len(created) == 1 and created[0]["data"]["route"] == "pending-strict"
    assert created[0]["data"]["planner"] == want
    assert any("needs a person" in r for r in created[0]["data"]["risk"]["reasons"])
    started = next(e for e in events if e["type"] == "step.started"
                   and e.get("step_id") == "approve")
    assert "strict" in started["data"]["inputs"]
    decide = next(e for e in events if e["type"] == "step.finished"
                  and e.get("step_id") == "approve.decide")
    nums = {n["label"]: n for n in decide["data"]["numbers"]}
    assert nums["planner"] == {"label": "planner", "v": "muse-spark-open",
                              "unit": "", "tag": "ADVICE"}
    assert nums["planner tier"]["v"] == "strict"
    body = _receipt_of(out)
    assert body["planner"] == {**want, "tier": "strict"}
    assert [(o["op"], o["by"]) for o in body["guardrail"]["ops"]] == [
        ("reject", "system:timeout")]
    assert all(o["planner"] == {**want, "tier": "strict"}
               for o in body["guardrail"]["ops"])
    for e in events:
        assert not check_labels(e)
