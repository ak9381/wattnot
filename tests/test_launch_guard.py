"""The double-launch guard: O_EXCL lock + idempotent name lookup + persist-intent-before-POST.

All on CPU with no network: `list_fn` and `create_fn` are plain callables, so a "launch" is just
a recorded function call. These tests pin down the exact failure the old launcher had (two
launchers both passing an exists-check and both creating a VM)."""

from __future__ import annotations

import json

import pytest

from wattnot.proof.launch_guard import (
    AlreadyLaunched,
    LaunchLocked,
    find_existing_by_name,
    guarded_launch,
    launch_lock,
    write_intent,
)


def _clock():
    t = {"v": 1000.0}
    return lambda: t["v"], t


# --------------------------------------------------------------------------- the lock


def test_lock_is_exclusive(tmp_path):
    lock = tmp_path / "launch.lock"
    with launch_lock(lock):
        assert lock.exists()
        with pytest.raises(LaunchLocked):   # a second holder refuses, never clobbers
            with launch_lock(lock):
                pass
    assert not lock.exists()   # released on exit


def test_lock_records_holder_and_is_not_auto_stolen(tmp_path):
    lock = tmp_path / "launch.lock"
    # Simulate a crashed previous launch that left its lock behind.
    lock.write_text(json.dumps({"pid": 999999, "ts": 1.0, "name": "wattnot-run3"}))
    with pytest.raises(LaunchLocked) as e:
        with launch_lock(lock):
            pass
    assert "wattnot-run3" in str(e.value)   # surfaces the holder for a human to clear


# --------------------------------------------------------------------------- idempotency lookup


def test_find_existing_by_name():
    nodes = [{"id": 1, "name": "other"}, {"id": 2, "name": "wattnot-run3"}]
    assert find_existing_by_name(nodes, "wattnot-run3")["id"] == 2
    assert find_existing_by_name(nodes, "missing") is None
    assert find_existing_by_name([], "x") is None
    assert find_existing_by_name(None, "x") is None


# --------------------------------------------------------------------------- guarded_launch


def test_guarded_launch_creates_once_and_persists_intent_before_create(tmp_path):
    clock, _ = _clock()
    intent = tmp_path / "vm.intent.json"
    record = tmp_path / "vm.json"
    created = []

    def create_fn():
        # The whole point: intent is on disk BEFORE the create runs, so a crash here is
        # recoverable (the orphan VM can be found by name).
        assert intent.exists(), "intent must be persisted before the create POST"
        assert json.loads(intent.read_text())["name"] == "wattnot-run3"
        created.append(1)
        return {"id": 424242}

    res = guarded_launch(name="wattnot-run3", lock_path=tmp_path / "l", record_path=record,
                         intent_path=intent, list_fn=lambda: [], create_fn=create_fn, clock=clock)
    assert res["action"] == "created" and res["id"] == 424242
    assert created == [1]
    assert json.loads(record.read_text())["id"] == 424242


def test_guarded_launch_adopts_an_existing_vm_and_never_creates(tmp_path):
    # A VM named the same already exists at the provider -> idempotent adopt, no second create.
    def create_fn():
        raise AssertionError("must not create when a VM with this name already exists")

    res = guarded_launch(name="wattnot-run3", lock_path=tmp_path / "l",
                         record_path=tmp_path / "vm.json",
                         list_fn=lambda: [{"id": 7, "name": "wattnot-run3"}], create_fn=create_fn)
    assert res["action"] == "adopted" and res["id"] == 7


def test_guarded_launch_refuses_when_a_record_already_exists(tmp_path):
    record = tmp_path / "vm.json"
    record.write_text(json.dumps({"id": 55, "name": "wattnot-run3"}))

    def create_fn():
        raise AssertionError("must not create when a record already exists")

    res = guarded_launch(name="wattnot-run3", lock_path=tmp_path / "l", record_path=record,
                         list_fn=lambda: [], create_fn=create_fn)
    assert res["action"] == "already-recorded" and res["id"] == 55


def test_two_launchers_cannot_both_create_the_toctou_fix(tmp_path):
    """The exact old bug: two launchers interleave. With the lock, the second refuses while the
    first holds it, so only one VM is ever created."""
    record = tmp_path / "vm.json"
    lock = tmp_path / "launch.lock"
    created = []

    def create_fn_inner():
        created.append(1)
        return {"id": 1}

    # Launcher A takes the lock and, before finishing, launcher B tries to come in.
    with launch_lock(lock):
        with pytest.raises(LaunchLocked):
            guarded_launch(name="wattnot-run3", lock_path=lock, record_path=record,
                           list_fn=lambda: [], create_fn=create_fn_inner)
    assert created == []  # B never created anything while A held the lock


def test_sequential_relaunch_is_idempotent_via_record(tmp_path):
    record = tmp_path / "vm.json"
    lock = tmp_path / "launch.lock"
    calls = {"n": 0}

    def create_fn():
        calls["n"] += 1
        return {"id": 100 + calls["n"]}

    r1 = guarded_launch(name="vm1", lock_path=lock, record_path=record,
                        list_fn=lambda: [], create_fn=create_fn)
    r2 = guarded_launch(name="vm1", lock_path=lock, record_path=record,
                        list_fn=lambda: [], create_fn=create_fn)
    assert r1["action"] == "created" and r2["action"] == "already-recorded"
    assert calls["n"] == 1 and r1["id"] == r2["id"]


def test_write_intent_is_atomic_json(tmp_path):
    p = tmp_path / "x.intent.json"
    write_intent(p, {"name": "n", "ts": 1.0})
    assert json.loads(p.read_text())["name"] == "n"
    assert not p.with_suffix(".json.tmp").exists()  # temp cleaned up
