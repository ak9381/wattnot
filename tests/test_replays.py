"""The four demo replays load in judge replay mode with no live calls, and match their manifests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wattnot.dash.server import create_app
from wattnot.report.eu import check_receipt_file

ROOT = Path(__file__).resolve().parents[1] / "replays"
NAMES = ["modal-r1", "modal-r2", "baseten-r1", "baseten-r2"]


def test_index_lists_all_four():
    text = (ROOT / "INDEX.md").read_text()
    for n in NAMES:
        assert f"replays/{n}/events.jsonl" in text


@pytest.mark.parametrize("name", NAMES)
def test_manifest_checksums_and_signatures(name):
    d = ROOT / name
    m = json.loads((d / "manifest.json").read_text())
    assert m["label"] == "REPLAY" and m["live_calls"] is False
    for f in m["files"]:
        assert hashlib.sha256((d / f["file"]).read_bytes()).hexdigest() == f["sha256"], f["file"]
    assert check_receipt_file(d / "receipt.json")[1] is True
    if (d / "energy-receipt.json").exists():
        assert check_receipt_file(d / "energy-receipt.json")[1] is True
    assert (d / "eu_report.html").stat().st_size > 0


@pytest.mark.parametrize("name", NAMES)
def test_events_keep_labels(name):
    lines = [json.loads(x) for x in (ROOT / name / "events.jsonl").read_text().splitlines()]
    assert lines
    for e in lines:
        assert e["replay"]["label"] == "REPLAY" and e["replay"]["source"]
        if e["type"] == "step.finished":  # step.started "label" is a display caption on Baseten
            assert e["data"]["label"] in ("MEASURED", "ESTIMATE", "MOCK", "REPLAY")


@pytest.mark.parametrize("name", NAMES)
def test_judge_replay_mode_loads(name):
    c = TestClient(create_app(events=ROOT / name / "events.jsonl", replay_only=True))
    h = c.get("/api/dash/health").json()
    assert h["mode"] == "replay" and h["replay_only"] and h["meta_calls"] is False
    assert h["events_name"] == "events.jsonl"
    m = c.get("/api/dash/meta/health").json()
    assert m["state"] == "replay_only" and m["called"] is False
