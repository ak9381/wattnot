"""WATTNOT_ONLY_BLOCKS limits the plan; WATTNOT_KEEP_VM=1 keeps the VM even at the end of the plan."""
from wattnot.longrun import driver as D


def test_only_blocks_filters_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("WATTNOT_MAX_HOLDOUT", "1")
    monkeypatch.setenv("WATTNOT_ONLY_BLOCKS", "h1-holdout")
    assert [b.id for b in D.plan(tmp_path, "glimmer-test", "max")] == ["h1-holdout"]
    monkeypatch.delenv("WATTNOT_ONLY_BLOCKS")
    assert len(D.plan(tmp_path, "glimmer-test", "max")) > 1


def _run(tmp_path, monkeypatch, keep):
    if keep:
        monkeypatch.setenv("WATTNOT_KEEP_VM", "1")
    else:
        monkeypatch.delenv("WATTNOT_KEEP_VM", raising=False)
    vm = D.FakeVm()
    st = D.Driver(tmp_path, D.plan(tmp_path), vm, label="MOCK", run=lambda b: 0, max_hours=18.9,
                  balance_inr=lambda: 9000, hours_used=lambda: 0, sleep=lambda s: None,
                  log=lambda m: None).go()
    return vm, [e["kind"] for e in st["events"]]


def test_keep_vm_never_deletes_at_end(tmp_path, monkeypatch):
    vm, kinds = _run(tmp_path / "a", monkeypatch, keep=False)
    assert vm.deleted and "vm-deleted" in kinds
    vm, kinds = _run(tmp_path / "b", monkeypatch, keep=True)
    assert not vm.deleted and "vm-deleted" not in kinds and "vm-kept" in kinds
