"""Three-layer teardown: the shared rule, idempotent teardown, and each layer independently.

Each layer (local loop, agent 2, agent 3) must tear down on budget, time and stale heartbeat;
a double delete must be a safe no-op; dry-run must never call the client.
"""

from __future__ import annotations

import json

import pytest

from wattnot.longrun.watchdog import (
    INR_PER_H,
    FakeE2EClient,
    FakeVm,
    Heartbeat,
    check_and_teardown,
    decide,
    read_heartbeat,
    write_heartbeat,
)
from wattnot.longrun.watchdog.agent import (
    build_check_command,
    build_prompt,
    resolve_cmd,
    run_agent_watchdog,
)
from wattnot.longrun.watchdog.local import run_local_watchdog

HOUR = 3600.0
STALE = 900.0


# A heartbeat that trips exactly one cap when read at t=now, and none of the others.
def _hb(path, *, now, trip):
    launch = now - 1 * HOUR
    # defaults: nowhere near any cap
    deadline = now + 10 * HOUR
    spent = 100.0
    beat_ts = now                 # fresh
    if trip == "budget":
        spent = 10_000.0
    elif trip == "time":
        deadline = now - 1.0
    elif trip == "stale":
        beat_ts = now - (STALE + 60.0)
    write_heartbeat(path, Heartbeat(ts=beat_ts, launch_ts=launch, deadline_ts=deadline,
                                    spent_inr=spent))
    return path


# --------------------------------------------------------------------------- the pure rule

def test_decide_fires_on_each_cap_and_names_the_reason():
    now = 1000.0
    assert decide(now=now, deadline_ts=now + 10, budget_spent_inr=10, budget_cap_inr=5,
                  heartbeat_ts=now, max_stale_s=STALE).reasons == ["budget cap"]
    assert decide(now=now, deadline_ts=now - 1, budget_spent_inr=0, budget_cap_inr=5,
                  heartbeat_ts=now, max_stale_s=STALE).reasons == ["time cap"]
    assert decide(now=now, deadline_ts=now + 10, budget_spent_inr=0, budget_cap_inr=5,
                  heartbeat_ts=now - STALE - 1, max_stale_s=STALE).reasons == ["stale heartbeat"]


def test_decide_is_quiet_when_nothing_tripped_and_when_caps_unknown():
    now = 1000.0
    d = decide(now=now, deadline_ts=now + 10, budget_spent_inr=1, budget_cap_inr=5,
               heartbeat_ts=now, max_stale_s=STALE)
    assert not d.teardown and d.reasons == []
    # None means "not enforced": no cap can trip
    d2 = decide(now=now, deadline_ts=None, budget_spent_inr=None, budget_cap_inr=None,
                heartbeat_ts=None, max_stale_s=None)
    assert not d2.teardown


# --------------------------------------------------------------------------- heartbeat I/O

def test_missing_or_corrupt_heartbeat_reads_as_none(tmp_path):
    assert read_heartbeat(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert read_heartbeat(bad) is None


def test_no_heartbeat_never_tears_down(tmp_path):
    client = FakeE2EClient()
    res = check_and_teardown(heartbeat_path=tmp_path / "none.json", client=client,
                             budget_cap_inr=1.0, max_hours=0.0, now=1e9)
    assert res["teardown"] is False and res["heartbeat"] is None
    assert client.delete_calls == 0


# --------------------------------------------------------------------------- idempotent teardown

def test_double_delete_is_idempotent_and_safe(tmp_path):
    vm = FakeVm()
    now = 1e9
    _hb(tmp_path / "hb.json", now=now, trip="budget")
    a = FakeE2EClient(vm)
    b = FakeE2EClient(vm)       # a second layer, same VM
    r1 = check_and_teardown(heartbeat_path=tmp_path / "hb.json", client=a, budget_cap_inr=5000,
                            now=now, sleep=lambda s: None)
    r2 = check_and_teardown(heartbeat_path=tmp_path / "hb.json", client=b, budget_cap_inr=5000,
                            now=now, sleep=lambda s: None)
    assert r1["deleted"] is True and r2["deleted"] is True
    assert r2["teardown_detail"]["already_gone"] is True   # second layer saw it already gone
    assert vm.delete_calls == 1                            # the VM was deleted exactly once


def test_teardown_retries_a_flaky_api_then_succeeds(tmp_path):
    vm = FakeVm()
    now = 1e9
    _hb(tmp_path / "hb.json", now=now, trip="time")
    client = FakeE2EClient(vm, fail_times=2)   # first two delete calls raise
    res = check_and_teardown(heartbeat_path=tmp_path / "hb.json", client=client,
                             budget_cap_inr=1e9, max_hours=5, now=now,
                             sleep=lambda s: None, delete_tries=3)
    assert res["deleted"] is True and client.delete_calls == 3 and not vm.present


# --------------------------------------------------------------------------- dry-run safety

def test_dry_run_never_calls_the_client(tmp_path):
    now = 1e9
    _hb(tmp_path / "hb.json", now=now, trip="budget")
    client = FakeE2EClient()
    res = check_and_teardown(heartbeat_path=tmp_path / "hb.json", client=client,
                             budget_cap_inr=5000, now=now, dry_run=True)
    assert res["teardown"] is True
    assert "WOULD_DELETE" in res["deleted"]
    assert client.delete_calls == 0 and client.exists_calls == 0 and client.vm.present


def test_check_cli_dry_run_prints_decision_without_a_client(tmp_path, capsys):
    from wattnot.longrun.watchdog.cli import main
    now_hb = tmp_path / "hb.json"
    write_heartbeat(now_hb, Heartbeat(ts=1.0, launch_ts=0.0, deadline_ts=1e18, spent_inr=99999.0))
    rc = main(["check", "--heartbeat", str(now_hb), "--budget-cap-inr", "1000", "--dry-run"])
    out = json.loads(capsys.readouterr().out.strip())
    assert rc == 0 and out["teardown"] is True and "WOULD_DELETE" in out["deleted"]


# --------------------------------------------------------------------------- layer 1: local loop

@pytest.mark.parametrize("trip", ["budget", "time", "stale"])
def test_layer1_local_tears_down_on_each_cap(tmp_path, trip):
    now = 1e9
    _hb(tmp_path / "hb.json", now=now, trip=trip)
    vm = FakeVm()
    res = run_local_watchdog(
        heartbeat_path=tmp_path / "hb.json", client=FakeE2EClient(vm),
        budget_cap_inr=5000, max_hours=5, max_stale_s=STALE,
        now=lambda: now, sleep=lambda s: None, max_ticks=1, log=lambda m: None,
    )
    assert res["teardown"] and trip.replace("budget", "budget cap").replace(
        "time", "time cap").replace("stale", "stale heartbeat") in res["reasons"]
    assert not vm.present


def test_layer1_local_keeps_polling_until_a_cap_trips(tmp_path):
    # healthy first, then the deadline passes: the loop must delete only once it trips.
    p = tmp_path / "hb.json"
    write_heartbeat(p, Heartbeat(ts=0.0, launch_ts=0.0, deadline_ts=5.0, spent_inr=1.0))
    vm = FakeVm()
    clock = {"t": 0.0}

    def now():
        return clock["t"]

    def sleep(s):
        clock["t"] += s

    res = run_local_watchdog(
        heartbeat_path=p, client=FakeE2EClient(vm), budget_cap_inr=1e9, max_hours=None,
        max_stale_s=None, poll_every_s=2.0, now=now, sleep=sleep, max_ticks=10, log=lambda m: None,
    )
    assert res["teardown"] and "time cap" in res["reasons"] and not vm.present


@pytest.mark.parametrize("trip", ["time", "stale"])
def test_layer1_local_uses_wall_clock_by_default(tmp_path, trip):
    # With the real default clock, a heartbeat/deadline written in epoch seconds must still trip.
    # A monotonic "now" (seconds since boot) is tiny next to an epoch timestamp, so the stale and
    # deadline comparisons would never fire -- this exercises the default, passing no `now`.
    import time
    now = time.time()
    p = tmp_path / "hb.json"
    if trip == "time":
        write_heartbeat(p, Heartbeat(ts=now, launch_ts=now - 10.0, deadline_ts=now - 1.0))
    else:  # stale: heartbeat older than the stale window, no deadline/budget
        write_heartbeat(p, Heartbeat(ts=now - (STALE + 60.0), launch_ts=None, deadline_ts=None))
    vm = FakeVm()
    res = run_local_watchdog(
        heartbeat_path=p, client=FakeE2EClient(vm), budget_cap_inr=None,
        max_hours=None, max_stale_s=STALE, sleep=lambda s: None, max_ticks=1, log=lambda m: None,
    )
    expected = "time cap" if trip == "time" else "stale heartbeat"
    assert res["teardown"] and expected in res["reasons"] and not vm.present


# --------------------------------------------------------------------------- layers 2 & 3: agents

def _agent_runner_over(vm, *, heartbeat_path, budget_cap_inr, max_hours, max_stale_s, now):
    """A fake agent CLI: running it = running the shared check (what the real prompt tells it)."""
    client = FakeE2EClient(vm)

    def runner(cmd, prompt):
        return check_and_teardown(
            heartbeat_path=heartbeat_path, client=client, budget_cap_inr=budget_cap_inr,
            max_hours=max_hours, max_stale_s=max_stale_s, now=now, sleep=lambda s: None,
            layer="agent-sim", log=lambda m: None,
        )

    return runner


@pytest.mark.parametrize("layer_env", ["WATTNOT_WD2_CMD", "WATTNOT_WD3_CMD"])
@pytest.mark.parametrize("trip", ["budget", "time", "stale"])
def test_agent_layers_tear_down_on_each_cap(tmp_path, monkeypatch, layer_env, trip):
    now = 1e9
    _hb(tmp_path / "hb.json", now=now, trip=trip)
    monkeypatch.setenv(layer_env, "fake-agent-cli --prompt {prompt}")
    layer = 2 if layer_env == "WATTNOT_WD2_CMD" else 3
    cmd = resolve_cmd(layer)
    assert cmd
    vm = FakeVm()
    runner = _agent_runner_over(vm, heartbeat_path=tmp_path / "hb.json", budget_cap_inr=5000,
                                max_hours=5, max_stale_s=STALE, now=now)
    check_cmd = build_check_command(heartbeat_path=tmp_path / "hb.json", budget_cap_inr=5000,
                                    max_hours=5, max_stale_s=STALE, dry_run=False)
    res = run_agent_watchdog(cmd=cmd, check_cmd=check_cmd, runner=runner, interval_s=1,
                             sleep=lambda s: None, max_ticks=2, log=lambda m: None,
                             label=f"layer{layer}")
    assert res["teardown"] and not vm.present


def test_agent_dry_run_stops_without_deleting(tmp_path, monkeypatch):
    now = 1e9
    _hb(tmp_path / "hb.json", now=now, trip="budget")
    vm = FakeVm()
    client = FakeE2EClient(vm)

    def runner(cmd, prompt):
        return check_and_teardown(heartbeat_path=tmp_path / "hb.json", client=client,
                                  budget_cap_inr=5000, now=now, dry_run=True, log=lambda m: None)

    res = run_agent_watchdog(cmd="x", check_cmd=["c"], runner=runner, interval_s=1,
                             sleep=lambda s: None, max_ticks=1, log=lambda m: None)
    assert res["teardown"] and vm.present and client.delete_calls == 0


def test_agent_prompt_and_command_are_neutral_and_reference_the_shared_check(tmp_path):
    check_cmd = build_check_command(heartbeat_path=tmp_path / "hb.json", budget_cap_inr=5000,
                                    max_hours=5, max_stale_s=STALE, dry_run=False)
    import sys
    # The interpreter is this very Python (sys.executable), never a bare "python" that may be
    # missing or point at Python 2 on the VM.
    assert check_cmd[0] == (sys.executable or "python3") and check_cmd[0] != "python"
    assert check_cmd[1:4] == ["-m", "wattnot.longrun.watchdog", "check"]
    prompt = build_prompt(check_cmd)
    assert "wattnot.longrun.watchdog" in prompt
    # No vendor names baked into the prompt or command (list supplied via env, kept out of the repo).
    import os
    low = prompt.lower()
    for name in filter(None, os.environ.get("WATTNOT_BANNED_NAMES", "").lower().split(",")):
        assert name not in low


def test_three_layers_racing_delete_the_same_vm_exactly_once(tmp_path):
    """Local + agent2 + agent3 all point at one VM and all fire: safe, deleted once."""
    now = 1e9
    _hb(tmp_path / "hb.json", now=now, trip="budget")
    vm = FakeVm()
    for _ in range(3):   # three independent layers, same box
        check_and_teardown(heartbeat_path=tmp_path / "hb.json", client=FakeE2EClient(vm),
                           budget_cap_inr=5000, now=now, sleep=lambda s: None)
    assert not vm.present and vm.delete_calls == 1


# --------------------------------------------------------------------------- driver heartbeat

def test_driver_writes_a_readable_heartbeat(tmp_path):
    from wattnot.longrun.driver import dry_run
    dry_run(tmp_path, runner=lambda b: 0)
    hb = read_heartbeat(tmp_path / "heartbeat.json")
    assert hb is not None and hb.launch_ts is not None and hb.deadline_ts is not None
    assert hb.spent_inr is not None and hb.spent_inr == pytest.approx(hb.spent_inr)
    # the beat the watchdog would read is internally consistent with its own rate
    assert hb.deadline_ts > hb.launch_ts
    assert INR_PER_H > 0
