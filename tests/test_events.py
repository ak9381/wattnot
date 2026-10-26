"""Tests for the run event emitter and its JSONL writer.

Written before the emitter exists: they pin FR-EVT-01 (append to
``runs/<run_id>/events.jsonl``, fsync per event, every event validated
against ``run-event.schema.json``) and the module interface the trial
runner and API will depend on.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wattnot import schemas
from wattnot.events import schema
from wattnot.events.emitter import Emitter, new_run_id

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "runs" / "demo_run" / "events.jsonl"
RUN_ID = "run_20261027_101500_a1b2c3"


# --- a frozen clock and id so the stream is deterministic in tests ----------


def fixed_clock(start="2026-10-27T10:15:00Z"):
    """Return a callable that yields UTC datetimes one second apart."""

    base = datetime.fromisoformat(start.replace("Z", "+00:00"))
    counter = {"n": 0}

    def now() -> datetime:
        value = base.replace(microsecond=0)
        from datetime import timedelta

        value = value + timedelta(seconds=counter["n"])
        counter["n"] += 1
        return value

    return now


# --- run id -----------------------------------------------------------------


def test_new_run_id_matches_schema_pattern():
    rid = new_run_id(now=lambda: datetime(2026, 10, 27, 10, 15, 0, tzinfo=UTC))
    assert schema.RUN_ID_RE.match(rid), rid
    # It validates as a run_id when dropped into an event.
    event = {
        "schema_version": "wattnot.event/1",
        "run_id": rid,
        "seq": 0,
        "ts": "2026-10-27T10:15:00Z",
        "type": "run.started",
        "data": {"mode": "container"},
    }
    schemas.validate_event(event)


def test_new_run_ids_are_unique():
    clock = lambda: datetime(2026, 10, 27, 10, 15, 0, tzinfo=UTC)  # noqa: E731
    ids = {new_run_id(now=clock) for _ in range(50)}
    assert len(ids) == 50, "the random suffix should keep ids unique within a second"


# --- the emitter writes a valid stream --------------------------------------


def test_emit_assigns_seq_from_zero_and_increments(tmp_path):
    em = Emitter(RUN_ID, root=tmp_path, now=fixed_clock())
    e0 = em.emit("run.started", {"mode": "container", "model": "m"})
    e1 = em.emit("step.started", {"kind": "probe", "label": "Probe"}, step_id="probe")
    e2 = em.emit("run.finished", {"status": "ok"})
    em.close()
    assert [e0["seq"], e1["seq"], e2["seq"]] == [0, 1, 2]


def test_emit_stamps_schema_version_and_timestamp(tmp_path):
    em = Emitter(RUN_ID, root=tmp_path, now=fixed_clock())
    event = em.emit("run.started", {"mode": "container"})
    em.close()
    assert event["schema_version"] == "wattnot.event/1"
    # ts round-trips as an RFC 3339 instant.
    parsed = datetime.fromisoformat(event["ts"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_every_emitted_event_validates(tmp_path):
    em = Emitter(RUN_ID, root=tmp_path, now=fixed_clock())
    em.emit("run.started", {"mode": "container", "model": "m"})
    em.emit("step.started", {"kind": "probe", "label": "Probe"}, step_id="probe")
    em.emit("step.progress", {"pct": 60, "message": "reading"}, step_id="probe")
    em.emit("run.finished", {"status": "ok"})
    em.close()
    for event in schemas.load_run_log(em.path):
        schemas.validate_event(event)


def test_emit_writes_jsonl_one_object_per_line(tmp_path):
    em = Emitter(RUN_ID, root=tmp_path, now=fixed_clock())
    em.emit("run.started", {"mode": "container"})
    em.emit("run.finished", {"status": "ok"})
    em.close()
    lines = em.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        json.loads(line)  # each line parses on its own


def test_emit_path_is_runs_run_id_events_jsonl(tmp_path):
    em = Emitter(RUN_ID, root=tmp_path, now=fixed_clock())
    em.close()
    assert em.path == Path(tmp_path) / "runs" / RUN_ID / "events.jsonl"


def test_whole_log_passes_validate_run_log(tmp_path):
    em = Emitter(RUN_ID, root=tmp_path, now=fixed_clock())
    em.emit("run.started", {"mode": "container"})
    em.emit("step.started", {"kind": "probe", "label": "Probe"}, step_id="probe")
    em.emit("run.finished", {"status": "ok"})
    em.close()
    events = schemas.validate_run_log(em.path)
    assert len(events) == 3


# --- step ids and optional fields -------------------------------------------


def test_step_and_parent_step_ids_are_carried_through(tmp_path):
    em = Emitter(RUN_ID, root=tmp_path, now=fixed_clock())
    event = em.emit(
        "step.started",
        {"kind": "trial", "label": "Trial 01"},
        step_id="trial-01",
        parent_step_id="plan",
    )
    em.close()
    assert event["step_id"] == "trial-01"
    assert event["parent_step_id"] == "plan"


def test_run_started_accepts_compare_to(tmp_path):
    em = Emitter(RUN_ID, root=tmp_path, now=fixed_clock())
    event = em.emit(
        "run.started",
        {"mode": "container", "compare_to": "run_20261026_090000_abcdef"},
    )
    em.close()
    schemas.validate_event(event)
    assert event["data"]["compare_to"] == "run_20261026_090000_abcdef"


# --- validation happens before the write ------------------------------------


def test_bad_event_type_raises_and_writes_nothing(tmp_path):
    em = Emitter(RUN_ID, root=tmp_path, now=fixed_clock())
    with pytest.raises(schemas.ValidationError):
        em.emit("run.exploded", {"boom": True})
    em.close()
    # Nothing valid was emitted, so no file (or an empty one) exists.
    assert not em.path.exists() or em.path.read_text() == ""


def test_bad_event_does_not_consume_a_seq(tmp_path):
    em = Emitter(RUN_ID, root=tmp_path, now=fixed_clock())
    em.emit("run.started", {"mode": "container"})
    with pytest.raises(schemas.ValidationError):
        em.emit("not.a.type", {})
    good = em.emit("run.finished", {"status": "ok"})
    em.close()
    assert good["seq"] == 1, "a rejected event must not burn a seq number"


def test_non_object_data_is_rejected(tmp_path):
    em = Emitter(RUN_ID, root=tmp_path, now=fixed_clock())
    with pytest.raises(schemas.ValidationError):
        em.emit("step.progress", ["not", "an", "object"])
    em.close()


# --- durability: fsync per event --------------------------------------------


def test_fsync_called_once_per_emitted_event(tmp_path, monkeypatch):
    calls = {"n": 0}
    real_fsync = os.fsync

    def counting_fsync(fd):
        calls["n"] += 1
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", counting_fsync)
    em = Emitter(RUN_ID, root=tmp_path, now=fixed_clock())
    em.emit("run.started", {"mode": "container"})
    em.emit("run.finished", {"status": "ok"})
    em.close()
    assert calls["n"] == 2


def test_emitter_is_a_context_manager(tmp_path):
    with Emitter(RUN_ID, root=tmp_path, now=fixed_clock()) as em:
        em.emit("run.started", {"mode": "container"})
        em.emit("run.finished", {"status": "ok"})
        path = em.path
    assert len(schemas.load_run_log(path)) == 2


def test_emit_after_close_raises(tmp_path):
    em = Emitter(RUN_ID, root=tmp_path, now=fixed_clock())
    em.close()
    with pytest.raises(ValueError):
        em.emit("run.started", {"mode": "container"})


# --- reopening an existing run appends, keeping seq strictly increasing ------


def test_reopen_continues_seq_and_appends(tmp_path):
    with Emitter(RUN_ID, root=tmp_path, now=fixed_clock()) as em:
        em.emit("run.started", {"mode": "container"})
        em.emit("step.started", {"kind": "probe", "label": "Probe"}, step_id="probe")
    again = Emitter(RUN_ID, root=tmp_path, now=fixed_clock("2026-10-27T11:00:00Z"))
    event = again.emit("run.finished", {"status": "ok"})
    again.close()
    assert event["seq"] == 2, "a reopened emitter resumes after the last seq on disk"
    events = schemas.validate_run_log(event_path := again.path)
    assert [e["seq"] for e in events] == [0, 1, 2]
    assert event_path.read_text().strip().count("\n") == 2


# --- the committed demo fixture ---------------------------------------------


def test_fixture_run_exists():
    assert FIXTURE.is_file()


def test_fixture_run_is_a_valid_emitted_stream():
    # The fixture should look exactly like something this emitter produces:
    # every event valid, seq strictly increasing from zero.
    events = schemas.validate_run_log(FIXTURE)
    assert events[0]["type"] == "run.started"
    assert events[-1]["type"] == "run.finished"


def test_emitter_can_reproduce_the_fixture_shape(tmp_path):
    # Replay the fixture's (type, data, step ids) through the emitter and
    # confirm the rebuilt stream validates and matches seq-for-seq.
    source = schemas.load_run_log(FIXTURE)
    em = Emitter(source[0]["run_id"], root=tmp_path, now=fixed_clock())
    for src in source:
        em.emit(
            src["type"],
            src["data"],
            step_id=src.get("step_id"),
            parent_step_id=src.get("parent_step_id"),
        )
    em.close()
    rebuilt = schemas.validate_run_log(em.path)
    assert [e["seq"] for e in rebuilt] == [e["seq"] for e in source]
    assert [e["type"] for e in rebuilt] == [e["type"] for e in source]


# --- the schema façade the emitter uses -------------------------------------


def test_schema_exposes_frozen_event_types():
    assert "run.started" in schema.EVENT_TYPES
    assert "run.finished" in schema.EVENT_TYPES
    assert schema.SCHEMA_VERSION == "wattnot.event/1"


def test_schema_check_event_rejects_bad_event():
    with pytest.raises(schemas.ValidationError):
        schema.check_event({"type": "nope"})
