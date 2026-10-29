"""The shared core every watchdog layer uses: one pure rule, one idempotent teardown.

`decide` is pure (no I/O, no clock of its own): it takes the numbers and says whether to
tear down and why. `check_and_teardown` is the deterministic script each layer runs: it reads
the heartbeat the driver writes, calls `decide`, and (unless dry-run) deletes the VM through an
injectable client. The delete is idempotent, so running it from three layers at once is safe.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

# Same measured price the driver uses (E2E H100 + GST + 500 GB disk), so a spend estimate
# here matches the driver's. See long-run-plan.md.
INR_PER_H = 451.2
# A run is "stale" if the driver has not refreshed its heartbeat for this long. 15 min matches
# proof/teardown.py's dead window: a lost link that never comes back looks the same from here.
DEFAULT_MAX_STALE_S = 900.0


def _noop(*_a, **_k) -> None:
    pass


# --------------------------------------------------------------------------- the pure rule


@dataclass
class Decision:
    """Why (if at all) the VM should be torn down now. Pure value, safe to log."""

    teardown: bool
    reasons: list[str]          # any of: "budget cap", "time cap", "stale heartbeat"
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def decide(
    *,
    now: float,
    deadline_ts: float | None,
    budget_spent_inr: float | None,
    budget_cap_inr: float | None,
    heartbeat_ts: float | None,
    max_stale_s: float | None,
) -> Decision:
    """The one rule all three layers obey. Tear down if ANY cap is exceeded:

    * budget cap  : spend (INR) has reached the cap,
    * time cap    : the clock has reached the deadline,
    * stale beat  : the driver's heartbeat is older than `max_stale_s`.

    Any argument may be None ("not known / not enforced"), in which case that cap never trips.
    Nothing is read or deleted here; the caller acts on the decision.
    """
    reasons: list[str] = []
    if budget_cap_inr is not None and budget_spent_inr is not None and budget_spent_inr >= budget_cap_inr:
        reasons.append("budget cap")
    if deadline_ts is not None and now >= deadline_ts:
        reasons.append("time cap")
    age = None
    if heartbeat_ts is not None and max_stale_s is not None:
        age = now - heartbeat_ts
        if age > max_stale_s:
            reasons.append("stale heartbeat")
    detail = {
        "budget_spent_inr": budget_spent_inr,
        "budget_cap_inr": budget_cap_inr,
        "deadline_ts": deadline_ts,
        "seconds_to_deadline": None if deadline_ts is None else round(deadline_ts - now, 1),
        "heartbeat_age_s": None if age is None else round(age, 1),
        "max_stale_s": max_stale_s,
    }
    return Decision(bool(reasons), reasons, detail)


# --------------------------------------------------------------------------- the heartbeat


@dataclass
class Heartbeat:
    """What the driver writes so the watchdogs know the run is alive and how far it has gone."""

    ts: float                       # when this heartbeat was written (epoch seconds)
    launch_ts: float | None = None
    deadline_ts: float | None = None
    spent_inr: float | None = None  # exact spend if the driver knows it; else an ESTIMATE
                                    # (rate x time) is derived by the watchdog — never metered
    block: str | None = None
    note: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def write_heartbeat(path: str | os.PathLike, hb: Heartbeat) -> None:
    """Atomic write, so a watchdog never reads a half-written beat."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(hb.as_dict()))
    os.replace(tmp, p)


def read_heartbeat(path: str | os.PathLike) -> Heartbeat | None:
    """Return the heartbeat, or None if it is missing or unreadable (never raise)."""
    p = Path(path)
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    if "ts" not in d:
        return None
    known = {f: d.get(f) for f in ("ts", "launch_ts", "deadline_ts", "spent_inr", "block", "note")}
    return Heartbeat(**known)


# --------------------------------------------------------------------------- the VM client


class E2EClient(Protocol):
    """The only VM operations a watchdog needs. The live adapter wraps the E2E delete API;
    tests pass `FakeE2EClient`. `delete` must be safe to call when the VM is already gone."""

    def exists(self) -> bool: ...
    def delete(self) -> None: ...


@dataclass
class FakeVm:
    """A shared stand-in VM. Point several `FakeE2EClient`s at one `FakeVm` to model three
    layers racing to delete the same box: the first delete wins, the rest are harmless no-ops."""

    present: bool = True
    delete_calls: int = 0

    def delete(self) -> None:
        self.delete_calls += 1
        self.present = False      # idempotent: deleting an absent VM is still absent

    def exists(self) -> bool:
        return self.present


class FakeE2EClient:
    """A test client over a `FakeVm`. `fail_times` makes the first N deletes raise (a flaky API),
    to exercise the teardown retry without a network."""

    def __init__(self, vm: FakeVm | None = None, *, fail_times: int = 0) -> None:
        self.vm = vm if vm is not None else FakeVm()
        self.fail_times = fail_times
        self.exists_calls = 0
        self.delete_calls = 0

    def exists(self) -> bool:
        self.exists_calls += 1
        return self.vm.exists()

    def delete(self) -> None:
        self.delete_calls += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("simulated transient delete failure")
        self.vm.delete()


# --------------------------------------------------------------------------- the shared script


def _teardown(client: E2EClient, *, tries: int, sleep: Callable[[float], None],
              log: Callable[[str], None]) -> dict:
    """Idempotent delete-and-verify. Safe to run from any layer, any number of times:
    if the VM is already gone (another layer, or an earlier run), it reports success at once."""
    last_err: str | None = None
    for i in range(max(1, tries)):
        try:
            if not client.exists():
                return {"deleted": True, "already_gone": True, "attempts": i, "error": None}
            client.delete()
            if not client.exists():
                return {"deleted": True, "already_gone": False, "attempts": i + 1, "error": None}
        except Exception as e:  # noqa: BLE001 - a flaky provider API must not crash the watchdog
            last_err = f"{type(e).__name__}: {e}"
            log(f"teardown attempt {i + 1} failed: {last_err}")
        if i + 1 < tries:
            sleep(15.0)
    # Best effort: report whether the VM is gone regardless of how we got here.
    try:
        gone = not client.exists()
    except Exception:  # noqa: BLE001
        gone = False
    return {"deleted": gone, "already_gone": gone and last_err is None, "attempts": tries,
            "error": last_err}


def check_and_teardown(
    *,
    heartbeat_path: str | os.PathLike,
    client: E2EClient | None,
    budget_cap_inr: float | None,
    max_hours: float | None = None,
    max_stale_s: float | None = DEFAULT_MAX_STALE_S,
    inr_per_h: float = INR_PER_H,
    now: float | None = None,
    dry_run: bool = False,
    layer: str = "?",
    delete_tries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = _noop,
) -> dict:
    """Run the full check once and (unless `dry_run`) tear the VM down if a cap tripped.

    This is the SINGLE deterministic routine every layer runs — the local loop calls it
    directly, and the agent layers call it through their CLI (`watchdog check`). Given the same
    heartbeat and clock it always makes the same decision, so the three layers cannot disagree.

    `dry_run=True` evaluates the rule and reports what it *would* do but never calls the client,
    so it can never touch a real API, launch or delete a VM.
    """
    now = time.time() if now is None else now
    hb = read_heartbeat(heartbeat_path)

    if hb is None:
        # No heartbeat means we cannot tell a not-yet-started run from a vanished one; refusing
        # to delete here is the safe default (the local layer still has the VM's own deadline,
        # and a truly stale run trips once a heartbeat has ever been written).
        res = {"layer": layer, "now": now, "heartbeat": None, "teardown": False,
               "reasons": [], "dry_run": dry_run, "deleted": None,
               "note": "no heartbeat found; not tearing down"}
        log(json.dumps(res))
        return res

    deadline_ts = hb.deadline_ts
    if deadline_ts is None and hb.launch_ts is not None and max_hours is not None:
        deadline_ts = hb.launch_ts + max_hours * 3600.0

    spent = hb.spent_inr
    spent_label = "MEASURED" if spent is not None else None
    if spent is None and hb.launch_ts is not None:
        # No exact figure from the driver: derive an ESTIMATE from elapsed time x the measured
        # all-in rate. This is an ESTIMATE, never a metered spend, and is labelled as such below.
        spent = max(0.0, (now - hb.launch_ts) / 3600.0) * inr_per_h
        spent_label = "ESTIMATE (rate x time)"

    d = decide(now=now, deadline_ts=deadline_ts, budget_spent_inr=spent,
               budget_cap_inr=budget_cap_inr, heartbeat_ts=hb.ts, max_stale_s=max_stale_s)

    res: dict = {"layer": layer, "now": now, "heartbeat": hb.as_dict(), "teardown": d.teardown,
                 "reasons": d.reasons, "detail": d.detail, "spent_inr_label": spent_label,
                 "dry_run": dry_run, "deleted": None}

    if not d.teardown:
        log(json.dumps(res))
        return res

    if dry_run:
        res["deleted"] = "WOULD_DELETE (dry-run; no API call)"
        log(json.dumps(res))
        return res

    if client is None:
        res["deleted"] = False
        res["error"] = "no client supplied for a live teardown"
        log(json.dumps(res))
        return res

    td = _teardown(client, tries=delete_tries, sleep=sleep, log=log)
    res.update({"deleted": td["deleted"], "teardown_detail": td})
    log(json.dumps(res))
    return res
