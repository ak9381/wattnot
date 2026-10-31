import json

from wattnot.longrun.driver import dry_run


def ok(b):
    return 0


def test_full_plan_with_link_drop_keeps_vm_and_finishes(tmp_path):
    st = dry_run(tmp_path, drop={"b2-wonder-sweep": 3}, runner=ok)
    s = {k: v["status"] for k, v in st["blocks"].items()}
    assert s["b5-second-model"] == "skipped"          # needs Akash's yes
    assert s["b2-wonder-sweep"] == "done" and st["blocks"]["b2-wonder-sweep"]["attempt"] == 2
    assert st["stopped"] == "finished" and st["sim"]["vm_deleted"]
    kinds = [e["kind"] for e in st["events"]]
    first = next(e for e in st["events"] if e["kind"] == "teardown-decision")
    assert first["delete"] is False and "answers again" in first["reason"]
    assert kinds[-1] == "vm-deleted"
    for b in ("b1-baseline-350", "b2-wonder-sweep", "b4-camera"):
        assert (tmp_path / "recordings" / b / "timeline.jsonl").exists()


def test_dead_vm_is_deleted_only_after_the_window(tmp_path):
    st = dry_run(tmp_path, dead_after="b1-baseline-350", runner=ok)
    assert st["stopped"] == "VM dead"
    d = next(e for e in st["events"] if e["kind"] == "teardown-decision")
    assert d["delete"] and d["probes"] >= 5 and "SSH dead" in d["reason"]
    assert st["blocks"]["b1-baseline-350"]["status"] == "lost"


def test_failed_block_is_retried_once(tmp_path):
    calls = []

    def flaky(b):
        calls.append(b.id)
        return 1 if calls.count(b.id) == 1 and b.id == "b3-tiers" else 0
    st = dry_run(tmp_path, runner=flaky)
    assert calls.count("b3-tiers") == 2 and st["blocks"]["b3-tiers"]["status"] == "done"


def test_blocks_dropped_from_the_bottom_when_time_runs_out(tmp_path):
    st = dry_run(tmp_path, include_optional=("b5-second-model",), runner=ok, max_hours=15)
    s = {k: v["status"] for k, v in st["blocks"].items()}
    # with Glimmer in, 15 h cannot hold every block: later ones drop, nothing overruns
    assert "dropped" in s.values()
    assert st["sim"]["hours"] <= 15


def test_resume_skips_done_blocks(tmp_path):
    dry_run(tmp_path, runner=ok)
    seen = []
    import json
    p = tmp_path / "driver_state.json"
    s = json.loads(p.read_text())
    s["blocks"].pop("b9-buffer")
    s["stopped"] = None
    p.write_text(json.dumps(s))
    from wattnot.longrun.driver import Driver, FakeVm, plan
    Driver(tmp_path, plan(tmp_path), FakeVm(), label="MOCK", run=lambda b: seen.append(b.id) or 0,
           max_hours=18.9, balance_inr=lambda: 9000, hours_used=lambda: 0,
           sleep=lambda s: None, log=lambda m: None).go()
    assert seen == ["b9-buffer"]


def test_balance_floor_forces_teardown(tmp_path):
    from wattnot.longrun.driver import Driver, FakeVm, plan
    vm = FakeVm()
    st = Driver(tmp_path, plan(tmp_path), vm, label="MOCK", run=lambda b: 0, max_hours=18.9,
                balance_inr=lambda: 1400, hours_used=lambda: 0, sleep=lambda s: None,
                log=lambda m: None).go()
    assert st["stopped"] == "money rule" and vm.deleted
    d = next(e for e in st["events"] if e["kind"] == "teardown-decision")
    assert "balance floor" in d["reason"]


def test_glimmer_plan_runs_end_to_end(tmp_path):
    st = dry_run(tmp_path, model="glimmer", drop={"g2-wonder-sweep": 3}, runner=ok)
    s = {k: v["status"] for k, v in st["blocks"].items()}
    assert s["g5-fp8"] == "done" and "g6-topk" not in s
    assert st["stopped"] == "finished" and st["sim"]["hours"] <= 18.0


def test_glimmer_trims_tiers_when_short(tmp_path):
    st = dry_run(tmp_path, model="glimmer", runner=ok, max_hours=10.0)
    kinds = [(e["kind"], e.get("block")) for e in st["events"]]
    assert ("trimmed", "g3-tiers") in kinds or ("trimmed", "g2-wonder-sweep") in kinds
    assert st["blocks"]["g1a-pair"]["status"] == "done"   # the 1+1 pair always comes first
    assert st["sim"]["hours"] <= 10.0


def test_block_receipts_are_chained(tmp_path):
    import json
    dry_run(tmp_path, model="glimmer", runner=ok)
    a = json.loads((tmp_path / "recordings" / "g1a-pair" / "receipt.json").read_text())
    b = json.loads((tmp_path / "recordings" / "g1b-repeats" / "receipt.json").read_text())
    assert b["prev"] == a["sha256"] and "timeline.jsonl" in a["files"]


def test_glimmer_test_session_finishes_early_and_deletes(tmp_path):
    # 3.5 h booking: the plan ends well before the cap, and the VM is DELETED at once (not
    # stopped, not left for the deadline watchdog), so billing stops early
    st = dry_run(tmp_path, model="glimmer-test", runner=ok, max_hours=3.5)
    assert all(v["status"] == "done" for v in st["blocks"].values()), st["blocks"]
    assert st["stopped"] == "finished" and st["sim"]["vm_deleted"]
    assert st["sim"]["hours"] < 3.5 - 8 / 60
    kinds = [e["kind"] for e in st["events"]]
    assert kinds[-1] == "vm-deleted"
    last = [e for e in st["events"] if e["kind"] == "teardown-decision"][-1]
    assert last["delete"] is True and "finished" in last["reason"]


def test_early_finish_deletes_even_if_final_copy_fails(tmp_path):
    from wattnot.longrun.driver import Driver, FakeVm, plan_glimmer_test

    class NoCopyVm(FakeVm):
        def pull(self):
            self.pulls += 1
            return False

    vm = NoCopyVm()
    t = {"now": 0.0}

    def sleep(s):
        t["now"] += s

    d = Driver(tmp_path, plan_glimmer_test(tmp_path), vm, label="MOCK", run=lambda b, s: 0,
               max_hours=3.5, balance_inr=lambda: 10_000.0, hours_used=lambda: t["now"] / 3600,
               sleep=sleep, clock=lambda: t["now"])
    st = d.go()
    assert vm.deleted and st["stopped"] == "finished"
    last = [e for e in st["events"] if e["kind"] == "teardown-decision"][-1]
    assert last["delete"] and last["forced"] and "delete anyway" in last["reason"]


def test_glimmer_test_plan_fits_the_time_budget(tmp_path):
    from wattnot.longrun.driver import WATCHDOG_MARGIN_H, plan_glimmer_test
    p = plan_glimmer_test(tmp_path)
    total_min = sum(b.hours for b in p) * 60
    usable = 3.5 * 60 - WATCHDOG_MARGIN_H * 60          # 202 min
    core = sum(b.hours for b in p if b.id not in ("t2b-batch", "t2c-prefix")) * 60
    assert core <= 125 and total_min <= usable - 30      # >= 30 min slack for a slow Glimmer
    # core-proof-first order: setup and probe lead, extras after every core block, copy last
    ids = [b.id for b in p]
    assert ids[0] == "t0-setup" and ids[1] == "t0-probe" and ids[-1] == "t3-copy"
    assert ids.index("t1-sweep") < ids.index("t2-fp8") < ids.index("t2b-batch")
    assert ids.index("t2-spoken") < ids.index("t2b-batch") < ids.index("t2c-prefix")
    fp8 = next(b for b in p if b.id == "t2-fp8")
    assert "glimmer_serve.sh fp8" in " ".join(fp8.live)   # FP8 really restarts the server
    sweep = next(b for b in p if b.id == "t1-sweep")
    i = sweep.live.index("--sam")
    assert sweep.live[i + 1] == "live"


def test_glimmer_test_live_blocks_use_real_commands(tmp_path):
    from wattnot.longrun.driver import plan_glimmer_test
    for b in plan_glimmer_test(tmp_path):
        if b.live is not None:
            assert "--fake" not in b.live and "--dry-run" not in b.live, b.id


def test_fp8_eye_and_spoken_skip_when_fp8_dropped(tmp_path):
    # a budget that drops t2-fp8 for time but still has room for the 3-min gates: the dependent
    # FP8 eye/spoken must SKIP, not gate a candidate that never existed (the dry-run bug).
    st = dry_run(tmp_path, model="glimmer-test", runner=ok, max_hours=93 / 60)
    s = {k: v["status"] for k, v in st["blocks"].items()}
    assert s["t2-fp8"] == "dropped"
    assert s["t2-eye"] == "skipped" and s["t2-spoken"] == "skipped"
    for b in ("t2-eye", "t2-spoken"):
        assert "dependency not done" in st["blocks"][b]["why"]


def test_overrunning_block_is_killed_and_recorded(tmp_path):
    from wattnot.longrun.driver import Driver, FakeVm, plan
    calls = []

    def run(b, budget_s):          # a 2-arg runner gets the per-block time budget
        calls.append(b.id)
        assert budget_s > 0
        # b3's first attempt overruns its budget (rc 124); the retry is fine
        return 124 if (b.id == "b3-tiers" and calls.count("b3-tiers") == 1) else 0
    st = Driver(tmp_path, plan(tmp_path), FakeVm(), label="MOCK", run=run, max_hours=18.9,
                balance_inr=lambda: 9000, hours_used=lambda: 0.0, sleep=lambda s: None,
                log=lambda m: None).go()
    assert any(e["kind"] == "overran" and e["block"] == "b3-tiers" for e in st["events"])
    assert st["blocks"]["b3-tiers"]["status"] == "done"  # the retry succeeded


def test_final_copy_checksum_mismatch_flags_the_run_but_still_deletes(tmp_path):
    from wattnot.longrun.driver import Driver, FakeVm, plan_glimmer_test
    vm = FakeVm(mismatch=["t1-sweep/proof-x.json"])
    t = {"now": 0.0}

    def sleep(s):
        t["now"] += s

    d = Driver(tmp_path, plan_glimmer_test(tmp_path), vm, label="MOCK", run=lambda b, s: 0,
               max_hours=3.5, balance_inr=lambda: 10_000.0, hours_used=lambda: t["now"] / 3600,
               sleep=sleep, clock=lambda: t["now"])
    st = d.go()
    assert vm.deleted
    assert st["copy_verify"]["status"] == "FLAGGED"
    assert st["stopped"].endswith("copy checksum FLAGGED")
    rc = json.loads((tmp_path / "recordings" / "t3-copy" / "receipt.json").read_text())
    assert rc["copy_verify"]["status"] == "FLAGGED"


def test_clean_copy_is_marked_ok(tmp_path):
    st = dry_run(tmp_path, model="glimmer-test", runner=ok, max_hours=3.5)
    assert st["copy_verify"]["status"] == "OK"


def test_dry_sweep_takes_the_live_approval_path(tmp_path):
    from wattnot.longrun.driver import plan_glimmer_test
    b = {x.id: x for x in plan_glimmer_test(tmp_path)}
    dry, live = b["t1-sweep"].dry, b["t1-sweep"].live
    assert "--auto-approve" not in dry
    for flag in ("--approval-policy", "--guardrail-config", "--sign-key", "--warmup-s",
                 "--burn-in", "--counterbalance", "--policy-hold-s"):
        assert flag in dry and flag in live, flag
    assert dry[dry.index("--guardrail-config") + 1] == live[live.index("--guardrail-config") + 1]
    w = b["t1-wonder"]
    assert "sweep_to_trials.py" in w.dry[2] and "--fake" in w.dry[2] and "--live" in w.live[2]


def test_policy_warning_when_every_wonder_cap_is_over_the_limit(tmp_path):
    from wattnot.longrun.driver import approval_policy_warnings
    p = {"format": "wattnot.auto-approve/1", "enabled": True, "allowed_labels": ["MEASURED"],
         "kinds": {"power_cap": {"min_saving_pct": 3, "max_p99_increase_pct": 10,
                                 "max_cap_drop_pct": 45}}, "set_by": "x", "set_at": "2026-10-30T00:00:00Z", "max_per_day": 3}
    f = tmp_path / "p.json"
    f.write_text(json.dumps(p))
    assert approval_policy_warnings(f)
    p["kinds"]["power_cap"]["max_cap_drop_pct"] = 58
    f.write_text(json.dumps(p))
    assert approval_policy_warnings(f) == []


def test_live_sweep_retry_moves_the_old_event_log_aside(tmp_path):
    # a retry re-runs the same argv into the same live.jsonl: without --fresh orchestrate exits 2
    from wattnot.longrun.driver import plan_glimmer_test
    b = {x.id: x for x in plan_glimmer_test(tmp_path)}
    assert "--fresh" in b["t1-sweep"].live and "--fresh" in b["t1-sweep"].dry


def test_prefix_ttft_from_vllm_histogram():
    from wattnot.context_saver.prefix_cache import ttft_ms_from_metrics
    m0 = ('vllm:time_to_first_token_seconds_sum{model_name="m"} 1.0\n'
          'vllm:time_to_first_token_seconds_count{model_name="m"} 10\n')
    m1 = ('vllm:time_to_first_token_seconds_sum{model_name="m"} 3.0\n'
          'vllm:time_to_first_token_seconds_count{model_name="m"} 30\n')
    assert ttft_ms_from_metrics(m0, m1) == 100.0
    assert ttft_ms_from_metrics("", m1) is None
    assert ttft_ms_from_metrics(m1, m1) is None


def test_policy_warning_covers_the_deeper_caps(tmp_path):
    from wattnot.longrun.driver import approval_policy_warnings
    p = {"format": "wattnot.auto-approve/1", "enabled": True, "allowed_labels": ["MEASURED"],
         "kinds": {"power_cap": {"min_saving_pct": 3, "max_p99_increase_pct": 10,
                                 "max_cap_drop_pct": 57}}, "set_by": "x",
         "set_at": "2026-10-30T00:00:00Z", "max_per_day": 3}
    f = tmp_path / "p.json"
    f.write_text(json.dumps(p))
    w = approval_policy_warnings(f)
    assert w and "the deeper" in w[0] and "58" in w[0]


def test_sweep_waits_for_the_probe(tmp_path):
    from wattnot.longrun.driver import plan_glimmer_test
    b = {x.id: x for x in plan_glimmer_test(tmp_path)}
    assert "t0-probe" in b["t1-sweep"].depends_on


def test_prefix_change_pct_tolerates_one_missing_arm():
    from wattnot.context_saver.prefix_cache import _change_pct, ttft_ms_from_metrics
    assert _change_pct(400.0, None) is None
    m = ('vllm:time_to_first_token_seconds_sum 1.0\nvllm:time_to_first_token_seconds_count 10\n')
    m2 = ('vllm:time_to_first_token_seconds_sum 1.0\nvllm:time_to_first_token_seconds_count 20\n')
    assert ttft_ms_from_metrics(m, m2) is None


# --- RUN3 retest fixes --------------------------------------------------------------------


def test_host_block_activates_the_setup_venv_and_fails_loudly():
    # the venv h100_vm_setup.sh builds is /root/venv, NOT /root/wattnot/venv; a host block must
    # activate that path and must not swallow a missing-venv error (RUN3 rc 127).
    from wattnot.longrun.driver import host_inner
    s = host_inner(["python", "-m", "wattnot.vision.run"])
    assert "/root/venv/bin/activate" in s
    assert "2>/dev/null" not in s          # the error is no longer hidden
    assert "exit 97" in s                  # and a missing venv fails loudly


def test_live_sweep_uses_real_wonder_and_covers_the_grid(tmp_path):
    from wattnot.longrun.driver import plan_glimmer_test
    sweep = {b.id: b for b in plan_glimmer_test(tmp_path)}["t1-sweep"].live
    assert "--wonder-live" in sweep                       # real planner, not muse-wonder-mock
    caps = [int(x) for x in sweep[sweep.index("--grid") + 1].split(",")]
    assert caps == [375, 350, 325, 300]                   # the four-cap retest grid
    assert min(caps) >= 294                               # never below the 58% max_cap_drop floor
    for flag in ("--warmup", "--reps", "--sweep-budget-s"):
        assert flag in sweep                              # tuned so the grid fits the budget


def test_retest_plan_fits_two_hours_and_reruns_the_failures(tmp_path):
    from wattnot.longrun.driver import WATCHDOG_MARGIN_H, plan_glimmer_retest
    p = plan_glimmer_retest(tmp_path)
    ids = [b.id for b in p]
    assert ids[0] == "t0-setup" and ids[1] == "t0-probe" and ids[-1] == "t3-copy"
    for failed in ("t1-sweep", "t1-resume", "t1-eye", "t1-spoken", "t2-fp8", "t2-eye",
                   "t2-spoken", "t2b-batch", "t2c-prefix"):
        assert failed in ids
    assert "t1-wonder" not in ids and "t1-eu" not in ids   # these passed in RUN3
    total_min = sum(b.hours for b in p) * 60
    usable = 2.0 * 60 - WATCHDOG_MARGIN_H * 60
    assert total_min <= usable, (total_min, usable)
    sweep = next(b for b in p if b.id == "t1-sweep")
    assert sweep.live[sweep.live.index("--sweep-budget-s") + 1] == "840"   # tighter than full test
    assert sweep.live[sweep.live.index("--reps") + 1] == "1"


def test_retest_dry_run_finishes_and_deletes(tmp_path):
    st = dry_run(tmp_path, model="glimmer-test", plan_name="retest", runner=ok, max_hours=2.0)
    assert all(v["status"] == "done" for v in st["blocks"].values()), st["blocks"]
    assert st["stopped"] == "finished" and st["sim"]["vm_deleted"]


def test_t2b_batch_block_has_a_realistic_budget(tmp_path):
    from wattnot.longrun.driver import plan_glimmer_test
    b = {x.id: x for x in plan_glimmer_test(tmp_path)}["t2b-batch"]
    assert b.hours * 60 >= 18   # RUN3 overran the 15 min (1350 s hard) limit twice


def test_run_child_kills_the_group_on_timeout():
    # a control-machine child that overruns is killed (whole process group) and reported 124,
    # not left to wedge teardown (RUN3: a pull hung 14+ min).
    import sys
    import time

    from wattnot.longrun import driver
    t0 = time.monotonic()
    rc = driver.run_child([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1.0)
    assert rc == 124 and time.monotonic() - t0 < 15
    assert not driver._CHILD_PGIDS   # registry cleaned up
