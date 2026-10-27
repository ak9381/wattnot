"""A run that stops half way carries on from its checkpoint: same run id, same event log, finished
stages not repeated, the planner's pending answer not asked (or paid for) twice. Fake GPU."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("wattnot.sam.room")

from wattnot.events import Emitter  # noqa: E402
from wattnot.proof import orchestrate  # noqa: E402
from wattnot.proof.checkpoint import Checkpoint  # noqa: E402
from wattnot.proof.sshutil import SshDown  # noqa: E402
from wattnot.proof.transport import LocalTransport  # noqa: E402

BASE = ["--mode", "rehearsal", "--load-s", "1", "--profile", "quick", "--reps", "2",
        "--repeat", "1", "--concurrency", "8", "--warmup", "1", "--settle-s", "0.05",
        "--adaptive-budget-s", "30", "--no-adaptive", "--max-proposals", "3"]


class Flaky(LocalTransport):
    """Loses the link on the n-th call of `cmd` (once per process), like RUN2's hung session."""

    cmd, nth, seen = None, 0, 0
    calls: list[tuple[str, dict]] = []

    def run(self, args, on_line, **kw):
        Flaky.calls.append((args[0], {k: v for k, v in kw.items() if k in ("key", "skip")}))
        if args[0] == Flaky.cmd:
            Flaky.seen += 1
            if Flaky.seen == Flaky.nth:
                raise SshDown("test: no answer from the GPU host")
        return super().run(args, on_line, **kw)


@pytest.fixture(autouse=True)
def _flaky(monkeypatch):
    Flaky.cmd, Flaky.nth, Flaky.seen, Flaky.calls = None, 0, 0, []
    monkeypatch.setattr(orchestrate, "LocalTransport", Flaky)


def launch(tmp_path, *extra):
    out = tmp_path / "o"
    argv = [*BASE, "--out", str(out), "--live-file", str(tmp_path / "live.jsonl"), *extra]
    code = orchestrate.main(argv)
    return code, out


def events_of(out):
    (path,) = out.glob("runs/run_*/events.jsonl")
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_stop_in_the_sweep_then_resume_finishes_the_same_run(tmp_path):
    Flaky.cmd, Flaky.nth = "step", 2  # the link drops as the second cap is about to be measured
    code, out = launch(tmp_path)
    assert code == 1
    ck = Checkpoint.load(out / "checkpoint.json")
    assert ck.done("room_scan") and ck.done("baseline") and ck.done("quality_gate")
    assert not ck.done("cap_sweep")
    sw = ck.get("cap_sweep")
    assert len(sw["done_steps"]) == 1 and sw["pending"]["emitted"]  # one cap done, one asked
    first_run = ck.run_id
    before = events_of(out)
    assert before[-1]["type"] == "run.finished" and before[-1]["data"]["status"] == "failed"
    assert "--resume" in before[-1]["data"]["reason"]

    Flaky.cmd, Flaky.calls = None, []
    code, out = launch(tmp_path, "--resume")
    assert code == 0
    ev = events_of(out)
    assert Checkpoint.load(out / "checkpoint.json").run_id == first_run
    assert [e["seq"] for e in ev] == list(range(len(ev)))  # one log, numbering unbroken
    assert ev[-1]["type"] == "run.finished" and ev[-1]["data"]["status"] == "ok"
    assert sum(e["type"] == "run.started" for e in ev) == 1
    # finished stages were not repeated: their commands were not sent again
    sent = [c for c, _ in Flaky.calls]
    assert "snap" not in sent and "baseline" not in sent and "control" not in sent
    started = [e["step_id"] for e in ev if e["type"] == "step.started"]
    for once in ("room_scan", "cap_sweep.propose1", "cap_sweep.propose2"):
        assert started.count(once) == 1, once  # the pending proposal was not asked for again
    assert started.count("cap_sweep") == 1  # the sweep node was reopened, not started twice
    assert sum(e["type"] == "step.finished" and e["step_id"] == "cap_sweep" for e in ev) == 1
    assert any(e["type"] == "step.progress" and e["data"].get("sub") == "resume" for e in ev)
    assert any(e["type"] == "receipt.signed" for e in ev)
    # every event, including the ones around the restart, passes the frozen schema
    em = Emitter("run_20260101_000000_abcdef", root=tmp_path / "v")
    for e in ev:
        em.emit(e["type"], e["data"], step_id=e.get("step_id"),
                parent_step_id=e.get("parent_step_id"))
    live = [json.loads(x) for x in (tmp_path / "live.jsonl").read_text().splitlines()]
    assert len(live) == len(ev)  # the dashboard's file followed along


@pytest.mark.parametrize("cmd", ["baseline", "control"])
def test_stop_in_an_early_stage_then_resume(tmp_path, cmd):
    Flaky.cmd, Flaky.nth = cmd, 1
    code, out = launch(tmp_path)
    assert code == 1
    Flaky.cmd, Flaky.calls = None, []
    code, out = launch(tmp_path, "--resume")
    assert code == 0
    ck = Checkpoint.load(out / "checkpoint.json")
    assert ck.done("finished") and ck.resumes == 1
    sent = [c for c, _ in Flaky.calls]
    assert "snap" not in sent  # the room scan was done before the stop
    assert sent[0] == cmd  # the interrupted stage is the first thing run again


def test_resume_of_a_finished_run_does_nothing(tmp_path):
    code, out = launch(tmp_path)
    assert code == 0
    n = len(events_of(out))
    Flaky.calls = []
    code, out = launch(tmp_path, "--resume")
    assert code == 0 and Flaky.calls == [] and len(events_of(out)) == n


def test_resume_without_a_checkpoint_starts_a_new_run(tmp_path):
    code, out = launch(tmp_path, "--resume")
    assert code == 0 and Checkpoint.load(out / "checkpoint.json").resumes == 0


def test_changed_measurement_settings_are_refused(tmp_path):
    Flaky.cmd, Flaky.nth = "step", 1
    launch(tmp_path)
    Flaky.cmd = None
    with pytest.raises(SystemExit):  # argparse error: the message names what changed
        launch(tmp_path, "--resume", "--reps", "5")


def test_auto_resume_rides_out_one_lost_link(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate.time, "sleep", lambda s: None)
    Flaky.cmd, Flaky.nth = "step", 2
    code, out = launch(tmp_path, "--auto-resume", "1")
    assert code == 0
    ev = events_of(out)
    assert sum(e["type"] == "run.finished" for e in ev) == 1  # no failed finish in between
    assert Checkpoint.load(out / "checkpoint.json").resumes == 1


def test_vm_commands_carry_a_key_and_the_measurement_profile(tmp_path):
    launch(tmp_path, "--profile", "long", "--reps", "2", "--warmup-s", "0", "--repeat", "1",
           "--control-blocks", "1", "--no-counterbalance")
    keys = [kw.get("key") for _, kw in Flaky.calls]
    assert all(k and "-" in k for k in keys) and len(set(keys)) == len(keys)
    assert any(k.endswith("-step-600") or "-step-" in k for k in keys)
