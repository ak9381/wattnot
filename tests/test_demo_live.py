"""The recorded live demo: real Muse Spark decisions over EXAMPLE GPU numbers."""

from __future__ import annotations

import json
from pathlib import Path

from wattnot.schemas import validate_event

LIVE = Path(__file__).resolve().parents[1] / "web" / "run" / "demo-events.live.jsonl"


def _events():
    return [json.loads(x) for x in LIVE.read_text().splitlines()]


def test_live_demo_is_schema_valid_and_gpu_numbers_are_examples():
    ev = _events()
    for e in ev:
        validate_event(e)
    assert ev[0]["data"]["label"] == "EXAMPLE"
    assert all(e["data"]["label"] == "EXAMPLE" for e in ev if e["type"] == "telemetry.sample")
    assert ev[-1]["type"] == "run.finished"


def test_live_demo_decisions_came_from_muse_spark_within_limits():
    ds = [e["data"] for e in _events() if e["type"] == "decision"]
    assert 0 < len(ds) <= 6
    for d in ds:
        assert d["model"].startswith("muse-spark-1.3")
        assert d["tokens"]["input"] > 0 and 0 < d["tokens"]["output"] <= 600
        assert d["reason"]
        if d["choice"]["kind"] == "cap":
            assert 200 <= d["choice"]["power_cap_w"] <= 700
