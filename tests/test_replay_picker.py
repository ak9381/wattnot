"""The dashboard's "Replay a run" picker: list and events routes read replays/ from disk."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from wattnot.dash.server import create_app

ROOT = Path(__file__).resolve().parents[1]
ON_DISK = sorted(p.parent.name for p in (ROOT / "replays").glob("*/events.jsonl"))


def _client():
    return TestClient(create_app(replay_only=True))


def test_list_matches_disk_and_has_fields():
    j = _client().get("/api/dash/replays").json()
    assert j["live_calls"] is False and j["label"] == "REPLAY"
    ids = sorted(r["id"] for r in j["replays"])
    assert ids == ON_DISK and {"h100-max-1103", "modal-r1", "baseten-r1"} <= set(ids)
    for r in j["replays"]:
        assert r["platform"] in ("H100", "Modal", "Baseten")
        assert r["date"] and r["title"] and r["events"] > 0
        assert set(r["labels"]) <= {"MEASURED", "ESTIMATE", "SIMULATED", "MOCK"}
        assert r["cli"] == f"./run-local.sh replay {r['id']}"
    h = next(r for r in j["replays"] if r["id"] == "h100-max-1103")
    assert h["platform"] == "H100" and h["measured"] and h["power_samples"] > 0


def test_events_route_returns_recorded_events():
    c = _client()
    j = c.get("/api/dash/replays/modal-r1/events").json()
    n = sum(1 for line in (ROOT / "replays/modal-r1/events.jsonl").read_text().splitlines() if line.startswith("{"))
    assert len(j["events"]) == n and j["run"]["id"] == "modal-r1"


def test_unknown_and_traversal_ids_404():
    c = _client()
    for bad in ("nope", "..", "..%2Fdata", "%2e%2e"):
        assert c.get(f"/api/dash/replays/{bad}/events").status_code == 404


def test_health_follows_replay_run():
    j = _client().get("/api/dash/health", params={"run": "replay:baseten-r1"}).json()
    assert j["events"] is True and j["events_name"] == "events.jsonl"


def test_landing_page_mounts_picker():
    html = (ROOT / "web/dash/index.html").read_text()
    assert 'id="wn-replay"' in html and "replaypick.js" in html
    js = (ROOT / "web/dash/replaypick.js").read_text()
    assert "/api/dash/replays" in js and "REPLAY of" in js
