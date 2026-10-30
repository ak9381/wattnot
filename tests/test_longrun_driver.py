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


def test_glimmer_test_session_fits_two_hours(tmp_path):
    from wattnot.longrun.driver import plan_glimmer_test
    opt = {b.id for b in plan_glimmer_test(tmp_path) if b.optional}
    st = dry_run(tmp_path, model="glimmer-test", runner=ok, max_hours=2.0)
    for k, v in st["blocks"].items():
        assert v["status"] == ("skipped" if k in opt else "done"), (k, v["status"])
    assert opt and all(st["blocks"][o]["status"] == "skipped" for o in opt)  # sweep off by default
    assert st["stopped"] == "finished" and st["sim"]["hours"] <= 2.0


def test_glimmer_test_plan_fits_the_time_budget(tmp_path):
    from wattnot.longrun.driver import plan_glimmer_test
    p = plan_glimmer_test(tmp_path)
    # the committed 2 h plan is the non-optional blocks; the batch sweep is opt-in and excluded
    total_min = sum(b.hours for b in p if not b.optional) * 60
    # must fit the ~112 usable min (2 h minus the 8 min watchdog margin) and use the window
    assert total_min <= 110
    assert 100 <= total_min
    # GPU-first order: setup and probe lead, copy-back is last
    ids = [b.id for b in p]
    assert ids[0] == "t0-setup" and ids[1] == "t0-probe" and ids[-1] == "t3-copy"
    assert ids.index("t1-sweep") < ids.index("t2-fp8")  # BF16 sweep before the FP8 swap
    # prefix-cache A/B is NOT in the 2 h test (it moved to the long run)
    assert not any("prefix" in b.id for b in p)


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
