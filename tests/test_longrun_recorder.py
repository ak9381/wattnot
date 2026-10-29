import json

from wattnot.longrun.recorder import Capture, Recorder, replay


def _ev(i, step="s"):
    return json.dumps({"schema_version": "wattnot.event/1", "seq": i, "type": "step.progress",
                       "ts": f"2026-11-01T00:00:{i:02d}.000Z", "step_id": f"{step}{i % 2}",
                       "data": {"status": "ok"}}) + "\n"


def test_each_block_gets_only_its_events(tmp_path):
    live = tmp_path / "live.jsonl"
    live.write_text(_ev(0))
    r = Recorder(tmp_path, live, label="MOCK")
    r.begin("b1")
    with live.open("a") as f:
        f.write(_ev(1) + _ev(2))
    d1 = r.end({"x": 1})
    r.begin("b2")
    with live.open("a") as f:
        f.write(_ev(3) + '{"half')          # half-written line is left for later
    d2 = r.end()
    assert len((d1 / "timeline.jsonl").read_text().splitlines()) == 2
    assert len((d2 / "timeline.jsonl").read_text().splitlines()) == 1
    st = json.loads((d1 / "state.json").read_text())
    assert st["events"] == 2 and st["result"] == {"x": 1}
    meta = json.loads((d1 / "meta.json").read_text())
    assert meta["label"] == "MOCK" and "?src=" in meta["replay"]


def test_replay_keeps_pacing(tmp_path):
    tl = tmp_path / "t.jsonl"
    tl.write_text(_ev(1) + _ev(3) + _ev(4))
    slept = []
    n = replay(tl, tmp_path / "out.jsonl", speed=2, sleep=slept.append)
    assert n == 3 and slept == [1.0, 0.5]
    assert (tmp_path / "out.jsonl").read_text() == tl.read_text()


def test_capture_commands_and_skip(tmp_path, monkeypatch):
    c = Capture("http://x/", tmp_path / "s.mp4")
    cmds = c.commands()
    assert cmds[0][0] == "Xvfb" and "--kiosk" in cmds[1] and cmds[2][0] == "ffmpeg"
    monkeypatch.setattr("wattnot.longrun.recorder.Capture.available",
                        staticmethod(lambda: "ffmpeg"))
    r = Recorder(tmp_path, tmp_path / "live.jsonl", label="MOCK")
    assert r.begin("cam", capture=True)["capture_skipped"] == "missing: ffmpeg"
    r.end()


def test_capture_start_stop_with_fake_procs(tmp_path, monkeypatch):
    monkeypatch.setattr("wattnot.longrun.recorder.Capture.available", staticmethod(lambda: None))
    started = []

    class P:
        def __init__(self, cmd, **kw):
            self.cmd, self.stdin, self.done = cmd, kw.get("stdin") and type(
                "S", (), {"write": lambda s, b: None, "flush": lambda s: None})(), False
            started.append(self)

        def terminate(self):
            self.done = True

        def wait(self, timeout=None):
            self.done = True

        def kill(self):
            self.done = True
    r = Recorder(tmp_path, tmp_path / "live.jsonl", label="MOCK")
    assert r.begin("cam", capture=True, popen=P, sleep=lambda s: None)["capture"]
    r.end()
    assert len(started) == 3 and all(p.done for p in started)


def test_rewritten_live_file_is_taken_whole(tmp_path):
    live = tmp_path / "live.jsonl"
    live.write_text(_ev(0) + _ev(1) + _ev(2))
    r = Recorder(tmp_path, live, label="MOCK")
    r.begin("b")
    live.write_text(_ev(5) + _ev(6) + _ev(7) + _ev(8))   # a --fresh run restarts the file
    d = r.end()
    assert len((d / "timeline.jsonl").read_text().splitlines()) == 4
