import json

from wattnot.gateway import plug


def _w(p, rows):
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _run(rid, wl, t0, t1):
    return {"run_id": rid, "workload": wl, "without": {"joules": 10, "requests": 3, "t0": 0, "t1": 1},
            "with": {"joules": 9, "requests": 3, "t0": t0, "t1": t1}}


def test_gate_split_per_run_and_kept_after_restart(tmp_path):
    log = tmp_path / "savings.jsonl"
    _w(log, [{"status": 200, "ts": t, "actual": {}, "baseline": {}} for t in (101, 102, 103, 201, 202, 203)])
    _w(tmp_path / "sequential.runs.jsonl", [_run("a", "text", 100, 110), _run("b", "agent", 200, 210)])
    _w(tmp_path / "savings.shadow.jsonl", [{"ts": 101.5, "agree": True}, {"ts": 102.5, "agree": True},
                                            {"ts": 201.5, "agree": False}, {"ts": 202.5, "agree": False}])
    ev = {"event": "saver_off", "saver": "routing", "model": "m", "why": "more tokens", "ts": 202.6}
    _w(tmp_path / "savings.gate.jsonl", [ev])
    runs = plug.gate_by_run(log, None)   # after a restart: no live monitor, events come from disk
    assert [r["workload"] for r in runs] == ["text", "agent"]
    assert runs[0]["agreement"] == 1.0 and runs[0]["switched_off"] == []
    assert runs[1]["agreed"] == 0 and runs[1]["shadow_pairs"] == 2
    assert runs[1]["switched_off"][0]["saver"] == "routing" and runs[1]["switched_off"][0]["after_request"] == 2
    # the same event live and on disk is counted once
    assert len(plug.gate_by_run(log, {"gate": {"events": [ev]}})[1]["switched_off"]) == 1
