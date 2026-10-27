"""GET /api/dash/agent-waste: the trimmed benchmark card, read-only."""
from __future__ import annotations

from unittest import mock

from fastapi.testclient import TestClient

from wattnot.dash.server import create_app


def test_agent_waste_endpoint_serves_the_card():
    c = TestClient(create_app())
    r = c.get("/api/dash/agent-waste")
    assert r.status_code == 200
    body = r.json()
    assert body["partner"] == {"id": "meta", "name": "Meta Muse Spark"}
    assert isinstance(body["headline"], str) and body["headline"]
    assert set(body["rows"]) == {"calls per run", "tokens per run",
                                "wall time per run (s)", "energy per run (J)",
                                "cost per run ($)"}
    assert len(body["notes"]) == 3


def test_agent_waste_endpoint_is_404_without_a_results_file():
    with mock.patch("wattnot.dash.server.load_waste", return_value=None):
        r = TestClient(create_app()).get("/api/dash/agent-waste")
    assert r.status_code == 404


def test_agent_waste_endpoint_uses_whatever_the_loader_returns():
    fake = {"schema": "wattnot.agent-waste/1", "arms": {}, "on_vs_off": {},
            "spend_usd_ledger": 0.5, "generated": "2026-10-27T00:00:00Z"}
    with mock.patch("wattnot.dash.server.load_waste", return_value=fake):
        r = TestClient(create_app()).get("/api/dash/agent-waste")
    assert r.status_code == 200
    assert r.json()["spend_usd"] == 0.5
