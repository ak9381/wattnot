import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "graph_events_sidecar", Path(__file__).resolve().parents[1] / "scripts" / "graph_events_sidecar.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


def test_route_mapping():
    assert sc.block_route("p2-eye") == ("gates", "eye")
    assert sc.block_route("p2-spoken") == ("gates", "spoken")
    assert sc.block_route("p1-langchain") == ("agent", "langchain")
    assert sc.block_route("p1-agentwaste") == ("agent", "waste")
    assert sc.block_route("b2-wonder-sweep") == ("e2e_rig", "sweep")
    assert sc.block_route("p2-tiers") == ("workload", "p2-tiers")


def _w(out, state, hb):
    (out / "driver_state.json").write_text(json.dumps({"blocks": state}))
    (out / "heartbeat.json").write_text(json.dumps(hb))


def _events(out):
    return [json.loads(x) for x in (out / "live.jsonl").read_text().splitlines()]


def test_lifecycle_shape_and_sweep_skipped(tmp_path):
    (tmp_path / "live.jsonl").write_text('{"existing":1}\n')
    s = sc.Sidecar(tmp_path, run_id="t")
    _w(tmp_path, {"p1-phase": {"status": "failed", "attempt": 1}}, {"block": "p1-phase", "note": "in-block"})
    s.tick(); s.tick()
    _w(tmp_path, {"p1-phase": {"status": "done", "attempt": 2}, "p2-sweep": {"status": "pending"}},
       {"block": "p2-sweep", "note": "in-block"})
    s.tick()
    _w(tmp_path, {"p2-sweep": {"status": "done"}, "p2-eye": {"status": "pending"}},
       {"block": "p2-eye", "note": "in-block"})
    s.tick()
    _w(tmp_path, {"p2-eye": {"status": "done", "attempt": 1}}, {"block": "p2-eye", "note": "between"})
    s.tick()
    lines = (tmp_path / "live.jsonl").read_text().splitlines()
    assert lines[0] == '{"existing":1}'
    ev = [json.loads(x) for x in lines[1:]]
    assert [(e["type"], e["step_id"]) for e in ev] == [
        ("step.started", "p1-phase"), ("step.finished", "p1-phase"),
        ("step.started", "p2-eye"), ("step.finished", "p2-eye")]
    for e in ev:
        assert e["schema_version"] == "wattnot.event/1" and e["run_id"] == "t"
        assert e["data"]["node"] == "gates" or e["data"]["node"] == "workload"
        assert {"node", "sub", "kind", "partners"} <= set(e["data"])
    assert ev[1]["data"]["status"] == "ok" and [e["seq"] for e in ev] == [1, 2, 3, 4]
    assert not any("sweep" in (e["step_id"] or "") for e in ev)


def test_missing_files_never_raise(tmp_path):
    sc.Sidecar(tmp_path).tick()


def test_block_start_beat_without_note(tmp_path):
    s = sc.Sidecar(tmp_path, run_id="t")
    _w(tmp_path, {"p1-agentwaste": {"status": "done"}}, {"block": "p1-agentwaste", "note": None})
    s.tick()
    assert not (tmp_path / "live.jsonl").exists()
    _w(tmp_path, {"p1-langchain": {"status": "pending"}}, {"block": "p1-langchain", "note": None})
    s.tick()
    assert [e["step_id"] for e in _events(tmp_path)] == ["p1-langchain"]
