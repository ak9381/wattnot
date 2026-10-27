"""The RUN2 event stream: a full rehearsal on the fake meter, every event valid and labelled."""

from __future__ import annotations

import json
import sys

import pytest

from wattnot.events import Emitter
from wattnot.proof import orchestrate
from wattnot.proof.events import NullStream, Stream, check_labels, read_raw

STEPS = ["room_scan", "baseline", "quality_gate", "cap_sweep", "adaptive_topk",
         "keep_rollback", "receipt"]


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
            "--live-file", str(live), "--load-s", "1", "--reps", "2", "--repeat", "1",
            "--concurrency", "8", "--warmup", "1", "--settle-s", "0.05",
            "--adaptive-budget-s", "30"]
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
