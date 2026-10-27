"""Freeze tests for the canonical contracts.

These lock the three JSON Schemas at the repo root, the Python validators,
the generated TypeScript types, and the demo fixture run. If any of these
drift, a test here should go red before anything downstream breaks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wattnot import schemas
from wattnot._tsgen import render_typescript

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "schemas"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "runs" / "demo_run" / "events.jsonl"
TS_OUTPUT = REPO_ROOT / "web" / "types" / "schemas.ts"

RUN_ID = "run_20261027_101500_a1b2c3"


# --- schema files exist and load -------------------------------------------


def test_all_schemas_present_at_repo_root():
    names = {p.name for p in SCHEMA_DIR.glob("*.schema.json")}
    assert names == {
        "run-event.schema.json",
        "receipt.schema.json",
        "ui-view.schema.json",
        "decision.schema.json",
    }


def test_root_schemas_match_docs_copies():
    for name in ("run-event", "receipt", "ui-view", "decision"):
        root = json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text())
        doc = json.loads((REPO_ROOT / "docs" / "schemas" / f"{name}.schema.json").read_text())
        assert root == doc, f"{name} schema drifted from docs/schemas copy"


def test_load_schema_returns_the_frozen_versions():
    assert schemas.load_schema("event")["$id"] == "wattnot.event/1"
    assert schemas.load_schema("receipt")["$id"] == "wattnot.receipt/1"
    assert schemas.load_schema("ui-view")["$id"].endswith("ui-view.schema.json")
    assert schemas.load_schema("decision")["$id"] == "wattnot.decision/1"


def test_minimal_decision_validates():
    record = {
        "step": 1,
        "planner": "muse-spark",
        "allowed_caps": [400, 425],
        "proposed_cap_w": 400,
        "reason": "400 W is the next step down",
        "explanation": "Muse Spark proposed 400 W. The gate has not ruled yet.",
        "proposal_accepted": True,
        "fallback": False,
        "gate": "pending",
    }
    schemas.validate_decision(record)


def test_every_schema_is_itself_valid_json_schema():
    # Should not raise: each schema compiles under its declared draft.
    for name in schemas.SCHEMA_NAMES:
        schemas.validator_for(name)


# --- event validation -------------------------------------------------------


def minimal_event(**over):
    event = {
        "schema_version": "wattnot.event/1",
        "run_id": RUN_ID,
        "seq": 0,
        "ts": "2026-10-27T10:15:00Z",
        "type": "step.progress",
        "data": {"pct": 50, "message": "halfway"},
    }
    event.update(over)
    return event


def test_minimal_event_validates():
    schemas.validate_event(minimal_event())


def test_event_rejects_wrong_schema_version():
    with pytest.raises(schemas.ValidationError):
        schemas.validate_event(minimal_event(schema_version="wattnot.event/2"))


def test_event_rejects_bad_run_id():
    with pytest.raises(schemas.ValidationError):
        schemas.validate_event(minimal_event(run_id="run_bad"))


def test_event_rejects_negative_seq():
    with pytest.raises(schemas.ValidationError):
        schemas.validate_event(minimal_event(seq=-1))


def test_event_rejects_unknown_type():
    with pytest.raises(schemas.ValidationError):
        schemas.validate_event(minimal_event(type="run.exploded"))


def test_event_rejects_extra_top_level_field():
    with pytest.raises(schemas.ValidationError):
        schemas.validate_event(minimal_event(prompt="leaked"))


def test_event_rejects_missing_data():
    bad = minimal_event()
    del bad["data"]
    with pytest.raises(schemas.ValidationError):
        schemas.validate_event(bad)


def test_run_started_compare_to_null_still_validates():
    event = minimal_event(
        seq=0,
        type="run.started",
        data={"mode": "container", "model": "m", "compare_to": None},
    )
    schemas.validate_event(event)


def test_run_started_compare_to_run_id_validates():
    event = minimal_event(
        type="run.started",
        data={"mode": "container", "compare_to": RUN_ID},
    )
    schemas.validate_event(event)


# --- fixture run ------------------------------------------------------------


def test_fixture_exists_and_parses():
    events = schemas.load_run_log(FIXTURE)
    assert len(events) >= 10


def test_every_fixture_event_validates():
    for event in schemas.load_run_log(FIXTURE):
        schemas.validate_event(event)


def test_fixture_seq_is_strictly_increasing_from_zero():
    events = schemas.load_run_log(FIXTURE)
    seqs = [e["seq"] for e in events]
    assert seqs[0] == 0
    assert all(b > a for a, b in zip(seqs, seqs[1:], strict=False))


def test_fixture_single_run_id():
    events = schemas.load_run_log(FIXTURE)
    assert {e["run_id"] for e in events} == {RUN_ID}


def test_fixture_starts_started_ends_finished():
    events = schemas.load_run_log(FIXTURE)
    assert events[0]["type"] == "run.started"
    assert events[-1]["type"] == "run.finished"


def test_validate_run_log_accepts_fixture():
    events = schemas.validate_run_log(FIXTURE)
    assert len(events) == len(schemas.load_run_log(FIXTURE))


def test_validate_run_log_rejects_non_increasing_seq(tmp_path):
    bad = tmp_path / "events.jsonl"
    rows = [minimal_event(seq=0, type="run.started", data={"mode": "container"}),
            minimal_event(seq=0, type="run.finished", data={"status": "ok"})]
    bad.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    with pytest.raises(schemas.SeqError):
        schemas.validate_run_log(bad)


# --- receipt validation -----------------------------------------------------


def minimal_receipt(**over):
    block = {
        "params": {"max_num_seqs": 64},
        "reps": 5,
        "wh_per_req": {"mean": 1.0, "sd": 0.1},
        "p99_ms": 900.0,
        "quality": {"fixed_pass_rate": 1.0},
    }
    receipt = {
        "spec": "wattnot.receipt/1",
        "receipt_id": "rcpt_0001",
        "created_at": "2026-10-27T10:20:00Z",
        "run_id": RUN_ID,
        "subject": {"model": "m", "host": {}, "mode": "container", "workload": {}},
        "baseline": json.loads(json.dumps(block)),
        "candidate": json.loads(json.dumps(block)),
        "result": {
            "saving_pct": {"mean": 20.0, "ci_low": 12.0, "ci_high": 28.0},
            "welch": {"t": 3.0, "df": 7.0, "p_value": 0.01, "alpha": 0.05},
            "billable": True,
            "billed_saving_pct": 12.0,
        },
        "measurement": {"meter": "nvml", "hz": 10, "csv": [], "label": "MEASURED"},
        "planner_trace": [],
        "grade": "VALID",
        "integrity": {"canonicalization": "JCS RFC 8785", "sha256": "a" * 64},
    }
    receipt.update(over)
    return receipt


def test_minimal_receipt_validates():
    schemas.validate_receipt(minimal_receipt())


def test_receipt_rejects_prompt_text():
    with pytest.raises(schemas.ValidationError):
        schemas.validate_receipt(minimal_receipt(prompt="never store this"))


def test_receipt_rejects_bad_sha256():
    bad = minimal_receipt()
    bad["integrity"]["sha256"] = "xyz"
    with pytest.raises(schemas.ValidationError):
        schemas.validate_receipt(bad)


def test_receipt_rejects_unknown_grade():
    with pytest.raises(schemas.ValidationError):
        schemas.validate_receipt(minimal_receipt(grade="SUPERB"))


# --- ui-view validation -----------------------------------------------------


def test_minimal_ui_view_validates():
    view = {
        "spec": "wattnot.ui-view/1",
        "derived": {"from": ["run_events"], "signed": False, "in_receipt_hash": False},
        "matrix_cards": [],
        "planner_reasons": [],
    }
    schemas.validate_ui_view(view)


def test_ui_view_signed_must_be_false():
    view = {
        "spec": "wattnot.ui-view/1",
        "derived": {"from": ["receipts"], "signed": True, "in_receipt_hash": False},
        "matrix_cards": [],
        "planner_reasons": [],
    }
    with pytest.raises(schemas.ValidationError):
        schemas.validate_ui_view(view)


# --- generated TypeScript ---------------------------------------------------


def test_generated_ts_has_root_types():
    ts = render_typescript(schemas.load_all_schemas())
    assert "export interface RunEvent" in ts
    assert "export interface Receipt" in ts
    assert "export interface UiView" in ts
    assert "export interface PlannerDecision" in ts


def test_committed_ts_is_up_to_date():
    expected = render_typescript(schemas.load_all_schemas())
    assert TS_OUTPUT.exists(), "run scripts/gen_ts_types.py to create web/types/schemas.ts"
    assert TS_OUTPUT.read_text() == expected, "web/types/schemas.ts is stale; regenerate it"
