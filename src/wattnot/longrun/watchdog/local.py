"""Layer 1: the local watchdog loop on the control box.

This is the in-repo, CPU-testable sibling of the live loop in
hackathon/e2e-glimmer-test/ctl.py (`ctl.py watchdog`). Same job, but it runs the shared
`check_and_teardown` so its decision is identical to layers 2 and 3, and every seam (clock,
sleep, client) is injectable so the whole loop is tested without a VM.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .core import DEFAULT_MAX_STALE_S, INR_PER_H, E2EClient, check_and_teardown


def run_local_watchdog(
    *,
    heartbeat_path,
    client: E2EClient | None,
    budget_cap_inr: float | None,
    max_hours: float | None,
    max_stale_s: float | None = DEFAULT_MAX_STALE_S,
    inr_per_h: float = INR_PER_H,
    poll_every_s: float = 120.0,
    dry_run: bool = False,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    max_ticks: int | None = None,
    log: Callable[[str], None] = print,
) -> dict:
    """Poll the heartbeat until a cap trips and the VM is torn down (or `max_ticks` is hit).

    Returns the last check result. In `dry_run` the loop stops the first time the rule says
    teardown (it would have deleted), without ever calling the client.
    """
    tick = 0
    last: dict = {}
    while True:
        tick += 1
        last = check_and_teardown(
            heartbeat_path=heartbeat_path, client=client, budget_cap_inr=budget_cap_inr,
            max_hours=max_hours, max_stale_s=max_stale_s, inr_per_h=inr_per_h,
            now=now(), dry_run=dry_run, layer="layer1-local", sleep=sleep, log=log,
        )
        done = last["teardown"] and (dry_run or last.get("deleted") is True)
        if done:
            return last
        if max_ticks is not None and tick >= max_ticks:
            return last
        sleep(poll_every_s)


def _main(argv=None) -> int:
    import argparse

    from .cli import build_client

    ap = argparse.ArgumentParser(description="Layer 1 local watchdog loop")
    ap.add_argument("--heartbeat", required=True)
    ap.add_argument("--budget-cap-inr", type=float, default=None)
    ap.add_argument("--max-hours", type=float, default=None)
    ap.add_argument("--max-stale-s", type=float, default=DEFAULT_MAX_STALE_S)
    ap.add_argument("--poll-every-s", type=float, default=120.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    res = run_local_watchdog(
        heartbeat_path=a.heartbeat, client=None if a.dry_run else build_client(),
        budget_cap_inr=a.budget_cap_inr, max_hours=a.max_hours, max_stale_s=a.max_stale_s,
        poll_every_s=a.poll_every_s, dry_run=a.dry_run,
    )
    return 0 if res.get("teardown") else 0


if __name__ == "__main__":
    raise SystemExit(_main())
