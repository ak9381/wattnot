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
