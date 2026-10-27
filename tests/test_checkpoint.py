"""The run checkpoint: atomic, mergeable, and strict about changed settings."""

from __future__ import annotations

import json

import pytest

from wattnot.proof.checkpoint import Checkpoint, CheckpointError

FP = {"model": "m", "reps": 5, "grid": [600, 500]}


def test_create_mark_and_reload(tmp_path):
    p = tmp_path / "checkpoint.json"
    ck = Checkpoint.create(p, "run_20260101_000000_abcdef", FP)
    assert not ck.done("baseline") and ck.last_done() is None
    ck.mark("room_scan", snap={"idle": {"cap_w": 700}})
    ck.update("cap_sweep", k=2, tried=[600])
    ck.set_lines("abc-step-600", 41)
    again = Checkpoint.load(p, FP)
    assert again.run_id == ck.run_id and again.done("room_scan") and not again.done("cap_sweep")
    assert again.get("cap_sweep") == {"k": 2, "tried": [600]}
    assert again.get("room_scan")["snap"]["idle"]["cap_w"] == 700
    assert again.cmds == {"abc-step-600": 41} and again.last_done() == "room_scan"
    assert not list(tmp_path.glob("*.tmp"))  # the write was atomic: no leftovers


def test_changed_settings_are_refused(tmp_path):
    p = tmp_path / "c.json"
    Checkpoint.create(p, "r", FP)
    with pytest.raises(CheckpointError, match="reps"):
        Checkpoint.load(p, {**FP, "reps": 3})
    Checkpoint.load(p, FP)  # the same settings are fine
    Checkpoint.load(p)  # no fingerprint given: not checked


def test_unreadable_or_foreign_files_are_refused(tmp_path):
    p = tmp_path / "c.json"
    with pytest.raises(CheckpointError):
        Checkpoint.load(p)
    p.write_text("{not json")
    with pytest.raises(CheckpointError):
        Checkpoint.load(p)
    p.write_text(json.dumps({"version": 99}))
    with pytest.raises(CheckpointError, match="version"):
        Checkpoint.load(p)


def test_the_file_never_holds_secrets_it_was_not_given(tmp_path):
    p = tmp_path / "c.json"
    ck = Checkpoint.create(p, "r", FP)
    ck.mark("baseline", ok=True)
    text = p.read_text()
    for word in ("token", "api_key", "password", "backup"):
        assert word not in text.lower()
