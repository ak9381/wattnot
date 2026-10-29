"""When may the control machine delete the GPU VM? (the RUN3 lesson)

RUN3 (28 Oct 2026, 20:17 IST) lost SSH for about two minutes right after the model weights
finished downloading. The setup poller gave up, the driver treated that as fatal and deleted the
VM at once. Nothing proved the VM was dead, and nothing had been copied off it.

The rules here:

1. A lost link is not a dead VM. The VM counts as dead only when the provider API says it is not
   running, or SSH has failed every probe for `dead_window_s` (default 15 min, at least
   `min_probes` probes).
2. Results are copied before any delete. If the VM is alive we pull first; a delete waits until a
   pull succeeded (or there was nothing new to pull).
3. Money rules still win. The hard deadline and the balance floor delete the VM even when nothing
   could be copied, because an unbounded bill is worse than a lost block. The reason is recorded.

`plan_teardown` is pure: it gets probe functions and a clock, so every branch is tested without a
VM. The driver calls it and only then calls the provider's delete.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

DEAD_WINDOW_S = 900.0
PROBE_EVERY_S = 60.0
MIN_PROBES = 5
PULL_TRIES = 3


@dataclass
class TeardownDecision:
    delete: bool
    reason: str
    vm_state: str            # "alive" | "dead" | "unknown"
    copied: bool
    forced: bool = False     # money rule overrode the copy-first rule
    probes: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def plan_teardown(*, ssh_alive: Callable[[], bool], api_state: Callable[[], str | None],
                  pull: Callable[[], bool], why: str, hard_deadline_hit: bool = False,
                  balance_low: bool = False, finished: bool = False,
                  dead_window_s: float = DEAD_WINDOW_S, probe_every_s: float = PROBE_EVERY_S,
                  min_probes: int = MIN_PROBES, pull_tries: int = PULL_TRIES,
                  sleep: Callable[[float], None] = time.sleep,
                  clock: Callable[[], float] = time.monotonic) -> TeardownDecision:
    """Decide whether to delete the VM now. `why` is the event that asked for teardown.

    api_state() returns the provider's state string ("running", "stopped", "deleted", ...) or
    None if the API itself could not be read. pull() copies results to the box and returns True
    on success. Nothing here deletes anything."""
    probes: list[dict] = []
    money = hard_deadline_hit or balance_low

    def try_pull() -> bool:
        for _ in range(pull_tries):
            if pull():
                return True
            sleep(min(probe_every_s, 30.0))
        return False

    # Money first: still try one copy, but never wait the dead window.
    if money:
        copied = ssh_alive() and try_pull()
        rule = "hard deadline" if hard_deadline_hit else "balance floor"
        return TeardownDecision(True, f"{rule} reached ({why}); delete regardless", "unknown",
                                copied, forced=not copied, probes=probes)

    # Normal end of the run: copy, then delete. If the copy fails, keep the VM (the time and
    # balance watchdogs still bound the bill) and let a human or the next retry copy it.
    if finished:
        copied = try_pull()
        if copied:
            return TeardownDecision(True, "run finished, results copied", "alive", True)
        return TeardownDecision(False, "run finished but the copy failed: keep the VM until a "
                                "copy succeeds (watchdogs still bound the bill)", "alive", False)

    # A failure. Is the VM really gone?
    start = clock()
    while True:
        st = api_state()
        alive = ssh_alive()
        probes.append({"t": round(clock() - start, 1), "api": st, "ssh": alive})
        if st is not None and st.lower() not in ("running", "active", "up"):
            return TeardownDecision(True, f"provider says the VM is {st!r} ({why}); nothing "
                                    "left to copy", "dead", False, probes=probes)
        if alive:
            copied = try_pull()
            return TeardownDecision(False, f"VM answers again after {why}: results "
                                    f"{'copied' if copied else 'NOT copied yet'}; resume, do "
                                    "not delete", "alive", copied, probes=probes)
        if clock() - start >= dead_window_s and len(probes) >= min_probes:
            return TeardownDecision(True, f"SSH dead for {clock() - start:.0f} s over "
                                    f"{len(probes)} probes after {why}; VM unrecoverable",
                                    "dead", False, probes=probes)
        sleep(probe_every_s)
