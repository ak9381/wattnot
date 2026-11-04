import json
from wattnot.gateway.harnessfeed import export
from wattnot.dash import launch


def test_gateway_run_shows_as_harness_round(tmp_path, monkeypatch):
    log = tmp_path / "savings.jsonl"
    rec = {"ts": 1.0e9, "status": 200, "actual": {"completion_tokens": 50, "usd": 0.001, "joules": 100.0, "models": ["m"]},
           "baseline": {"model": "m"}, "labels": {"joules": "MEASURED (NVML)"}, "receipt_id": "gw_1"}
    log.write_text(json.dumps(rec) + "\n")
    log.with_name("savings.without.jsonl").write_text(json.dumps({"ts": 1.0e9, "joules": 200.0, "usd": 0.002}) + "\n")
    log.with_name("savings.shadow.jsonl").write_text(json.dumps({"agree": True}) + "\n")
    monkeypatch.setattr(launch, "ROOT", tmp_path)
    r = export(log, tmp_path / "out" / "gateway-runs" / "gw1", round_id="gw1", status="done")
    assert r["results"]["energy_saved_pct"] == 50.0 and r["label"] == "MEASURED"
    rounds = launch.rounds()
    assert rounds[0]["id"] == "own_gpu:gw1" and rounds[0]["arms"]["with_wattnot"]["energy"] == 100.0
    assert launch.events_path_for("own_gpu:gw1").name == "events.jsonl"
    types = [json.loads(l)["type"] for l in (tmp_path / "out/gateway-runs/gw1/events.jsonl").read_text().splitlines()]
    assert types.count("run.finished") == 2 and "step.finished" in types
