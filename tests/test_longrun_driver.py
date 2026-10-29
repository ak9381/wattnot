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
    st = dry_run(tmp_path, model="glimmer-test", runner=ok, max_hours=2.0)
    assert all(v["status"] == "done" for v in st["blocks"].values())
    assert st["stopped"] == "finished" and st["sim"]["hours"] <= 2.0
