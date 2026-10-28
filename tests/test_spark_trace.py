from pathlib import Path

from wattnot.planner.trace import from_file

EV = Path(__file__).resolve().parents[1] / "data" / "h100-run2" / "events" / "sweep.events.jsonl"


def test_run2_trace_finds_350():
    t = from_file(EV)
    assert t["n_calls"] == 4 and t["found_cap_w"] == 350
    assert [c["choice"] for c in t["calls"]] == ["cap 400 W", "cap 350 W", "cap 325 W", "stop"]
    assert t["calls"][2]["gate"]["passed"] is False and t["calls"][1]["gate"]["passed"] is True
    assert all(c["latency_s"]["label"] == "MEASURED" for c in t["calls"])
    assert t["total_usd"]["value"] > 0 and t["total_usd"]["label"] == "ESTIMATE"
