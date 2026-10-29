"""A three-layer teardown for the long-run H100 VM.

All three layers share ONE deterministic rule (`decide`) and ONE idempotent teardown
(`check_and_teardown`), so no layer can decide to kill the VM for a reason another layer
would not, and any layer deleting is safe even when the others try at the same time.

    layer 1  the local watchdog loop on the control box      (`local.run_local_watchdog`)
    layer 2  a headless coding-agent CLI (env WATTNOT_WD2_CMD) that periodically runs the
             shared check command                             (`agent.run_agent_watchdog`)
    layer 3  a second, different agent CLI (env WATTNOT_WD3_CMD), same contract

The honest limit (see docs/watchdogs.md): all three run on the same box, so the box dying
takes every layer with it. Box-independent fixes are proposed there.

Everything here is dry-run / CPU testable: `decide` is pure, teardown goes through an
injectable client (`FakeE2EClient` in tests), and `--dry-run` never touches a real API.
"""

from .core import (
    DEFAULT_MAX_STALE_S,
    INR_PER_H,
    Decision,
    E2EClient,
    FakeE2EClient,
    FakeVm,
    Heartbeat,
    check_and_teardown,
    decide,
    read_heartbeat,
    write_heartbeat,
)

__all__ = [
    "DEFAULT_MAX_STALE_S",
    "INR_PER_H",
    "Decision",
    "E2EClient",
    "FakeE2EClient",
    "FakeVm",
    "Heartbeat",
    "check_and_teardown",
    "decide",
    "read_heartbeat",
    "write_heartbeat",
]
