"""The live E2E client adapter and `watchdog check` subcommand.

`build_client` is the ONLY place that would touch the real provider. It is never called in
tests or in `--dry-run`; it lazily imports the E2E API helper and refuses to build if the
helper or credentials are absent, rather than pretending a VM could be deleted.
"""

from __future__ import annotations

import argparse

from .core import DEFAULT_MAX_STALE_S, check_and_teardown


class E2EApiClient:
    """Live adapter over the E2E delete API. Built only for a real teardown; never in tests.

    `vm_id` identifies the notebook/VM; `call` is the E2E request helper (reads the API key
    from env, never printed). `project` carries project_id/location. Mirrors
    hackathon/e2e-glimmer-test/ctl.py's delete path, but is only reached from a non-dry-run CLI.
    """

    def __init__(self, vm_id, call, **project) -> None:
        self.vm_id = vm_id
        self._call = call
        self._project = project

    def exists(self) -> bool:
        status, _ = self._call("GET", f"gpu/notebooks/{self.vm_id}/", **self._project)
        return status != 404

    def delete(self) -> None:
        self._call("DELETE", f"gpu/notebooks/{self.vm_id}/", **self._project)


def build_client():
    """Construct the live client, or raise with a clear message. Called only for a real delete."""
    raise SystemExit(
        "live teardown is not wired here: a real delete needs the E2E API helper and a VM id.\n"
        "Build an E2EApiClient(vm_id, call, project_id=..., location=...) from the control host\n"
        "(see hackathon/e2e-glimmer-test/ctl.py), or pass --dry-run. The watchdog will never\n"
        "launch or delete a VM from this repo checkout."
    )


def _cmd_check(a) -> int:
    client = None if a.dry_run else build_client()
    res = check_and_teardown(
        heartbeat_path=a.heartbeat, client=client, budget_cap_inr=a.budget_cap_inr,
        max_hours=a.max_hours, max_stale_s=a.max_stale_s, dry_run=a.dry_run,
        layer=a.layer, log=lambda _m: None,
    )
    import json
    print(json.dumps(res, default=str))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="wattnot.longrun.watchdog",
                                 description="Three-layer long-run VM teardown")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="run the shared check+teardown once, print JSON")
    c.add_argument("--heartbeat", required=True)
    c.add_argument("--budget-cap-inr", type=float, default=None)
    c.add_argument("--max-hours", type=float, default=None)
    c.add_argument("--max-stale-s", type=float, default=DEFAULT_MAX_STALE_S)
    c.add_argument("--layer", default="check")
    c.add_argument("--dry-run", action="store_true",
                   help="evaluate the rule but never call the API (no delete)")
    c.set_defaults(func=_cmd_check)

    a = ap.parse_args(argv)
    return a.func(a)
