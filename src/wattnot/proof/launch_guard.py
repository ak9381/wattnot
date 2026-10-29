"""Make launching a (paid) GPU VM safe against a double launch.

RUN3 (28 Oct) showed how a VM can be deleted by mistake; the mirror-image mistake is launching
*two* VMs and paying for both. The old launcher guarded with
``assert not os.path.exists(vm.json)`` and wrote the record only AFTER the create POST. That has
two holes:

* **TOCTOU race** — two launchers can both pass the ``exists`` check before either writes the
  record, and both POST. Two VMs, double the bill.
* **lost intent on a crash** — a crash between the POST and the record write leaves a running VM
  with no local record, so nothing knows to tear it down.

This module closes both with three mechanisms, kept pure and injectable so they test on CPU with
no network and no real VM:

1. **an O_EXCL lock file** — ``os.open(..., O_CREAT | O_EXCL)`` is atomic, so exactly one launcher
   can hold the lock at a time; a second attempt refuses instead of racing.
2. **an idempotency check** — before creating, list the project's existing VMs and, if one with
   the same name is already there (or a prior record exists), ADOPT it and do not create a second.
3. **persist-intent-before-POST** — the intent (name + timestamp) is written to disk *before* the
   create call, so a crash mid-create is recoverable: the operator (or a watchdog) can find the
   intent, look the VM up by name, and tear it down.

The canonical logic lives here and is unit-tested; the live launcher
(``hackathon/e2e-glimmer-test/ctl.py``) applies the same three mechanisms inline (it cannot import
this package on the control host).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class LaunchLocked(RuntimeError):
    """Raised when another launcher already holds the lock (or a stale lock is present)."""


class AlreadyLaunched(RuntimeError):
    """Raised when a record shows this launch already produced a VM (idempotent refusal)."""


@contextmanager
def launch_lock(path: str | os.PathLike, *, info: dict | None = None,
                clock: Callable[[], float] = time.time):
    """Hold an exclusive, atomic O_EXCL lock for the duration of the block.

    A second caller while the lock is held raises ``LaunchLocked`` (it never clobbers the holder).
    The lock file records who holds it (pid + timestamp + optional info) so a human can inspect or
    clear a stale lock by hand — we deliberately do NOT auto-steal a lock, because a wrongly
    stolen launch lock means paying for two GPUs.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"pid": os.getpid(), "ts": clock(), **(info or {})})
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        existing = _safe_read(p)
        raise LaunchLocked(
            f"launch lock {p} is already held: {existing}. Another launch is in progress, or a "
            f"previous one crashed — verify no VM is running, then remove the lock to retry.")
    try:
        os.write(fd, payload.encode())
        os.close(fd)
        yield p
    finally:
        try:
            os.unlink(p)
        except OSError:
            pass


def _safe_read(p: Path) -> str:
    try:
        return p.read_text()
    except OSError:
        return "<unreadable>"


def find_existing_by_name(nodes: Iterable[Any], name: str) -> dict | None:
    """Return the first node whose ``name`` matches, else None. This is the idempotency check:
    if the provider already has a VM with our name, we must not create a second one."""
    for n in nodes or []:
        if isinstance(n, dict) and n.get("name") == name:
            return n
    return None


def write_intent(path: str | os.PathLike, intent: dict) -> None:
    """Atomically record that a create is about to be attempted, BEFORE the create call, so a
    crash mid-create leaves a breadcrumb (the VM name) to find and tear down the orphan."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(intent))
    os.replace(tmp, p)


def guarded_launch(
    *,
    name: str,
    lock_path: str | os.PathLike,
    record_path: str | os.PathLike,
    list_fn: Callable[[], Iterable[Any]],
    create_fn: Callable[[], dict],
    intent_path: str | os.PathLike | None = None,
    record_write: Callable[[dict], None] | None = None,
    clock: Callable[[], float] = time.time,
    log: Callable[[str], None] = lambda _m: None,
) -> dict:
    """Launch a VM named ``name`` at most once. Returns a result dict with ``action`` one of
    ``"created"`` / ``"adopted"`` / ``"already-recorded"`` and the VM ``id``.

    Order (all under the O_EXCL lock, so no two launchers can interleave):
      1. if a record already exists -> ``already-recorded`` (never create again),
      2. list existing VMs; if one is named ``name`` -> ``adopted`` (never create a duplicate),
      3. write intent BEFORE the create,
      4. create, then write the record.
    ``create_fn`` must return a dict with at least an ``id``; ``list_fn`` returns the project's
    VM dicts. Both are injected so this is testable with no network.
    """
    record_path = Path(record_path)
    intent_path = Path(intent_path) if intent_path is not None else record_path.with_name(
        record_path.stem + ".intent.json")
    record_write = record_write or (lambda rec: record_path.write_text(json.dumps(rec)))

    with launch_lock(lock_path, info={"name": name}, clock=clock):
        if record_path.exists():
            rec = json.loads(_safe_read(record_path) or "{}")
            log(f"launch refused: a record already exists for {name} (id {rec.get('id')})")
            return {"action": "already-recorded", "id": rec.get("id"), "record": rec}

        existing = find_existing_by_name(list_fn(), name)
        if existing is not None:
            rec = {"id": existing.get("id"), "name": name, "adopted": True, "ts": clock()}
            record_write(rec)
            log(f"launch idempotent: a VM named {name} already exists (id {rec['id']}); adopted")
            return {"action": "adopted", "id": rec["id"], "record": rec}

        write_intent(intent_path, {"name": name, "ts": clock(), "stage": "pre-create"})
        log(f"intent persisted for {name}; creating")
        created = create_fn()
        rec = {"id": created.get("id"), "name": name, "created": True, "ts": clock(),
               **{k: v for k, v in created.items() if k != "id"}}
        record_write(rec)
        log(f"launch created {name} (id {rec['id']})")
        return {"action": "created", "id": rec["id"], "record": rec}
