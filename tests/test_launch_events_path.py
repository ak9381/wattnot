import json, os, time
from wattnot.dash import launch


def test_job_follows_newest_full_round_events(tmp_path, monkeypatch):
    data = tmp_path / "data"
    old = data / "smoke-r04" / "flow" / "runs" / "run_old"
    old.mkdir(parents=True)
    (old / "events.jsonl").write_text("{}\n")
    past = time.time() - 3600
    os.utime(old / "events.jsonl", (past, past))
    live = data / "r04.with.events.jsonl"
    live.write_text("{}\n")
    jobs = tmp_path / "jobs" / "j1"
    jobs.mkdir(parents=True)
    (jobs / "config.json").write_text(json.dumps({
        "config": {"provider": "modal_a100"},
        "cmd": "/usr/bin/python3 scripts/e2e/modal_stage.py --rounds r04 --sets ours"}))
    monkeypatch.setattr(launch, "ROOT", tmp_path)
    monkeypatch.setattr(launch, "JOBS_DIR", tmp_path / "jobs")
    rd = data / "smoke-r04"
    monkeypatch.setattr(launch, "rounds", lambda: [
        {"id": "modal_a100:r04", "events": launch.round_events(rd)}])
    assert launch._job_round("j1") == "modal_a100:r04"
    assert launch.events_path_for("job:j1") == live
