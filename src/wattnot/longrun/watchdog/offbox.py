"""Off-box backstops: layer 4 (a scheduled GitHub Actions cron) and the shared engine the
macOS layer (layer 5, scripts/mac_watchdog/) mirrors.

The on-box layers (1-3; see local.py / agent.py) all run on the control box, so the box dying
takes every one of them with it. Layers 4 and 5 run *somewhere else* — GitHub's hosted runners
and a Mac on a desk — and poll the SAME heartbeat the driver writes, now published to a URL
(a gist raw URL, or a file on a dedicated branch served by raw.githubusercontent) instead of a
local path. They reuse the SAME pure rule (`decide`) and the SAME idempotent teardown
(`check_and_teardown`) as every on-box layer, so an off-box layer can never invent a policy the
others would not.

Nothing here is enabled live. `offbox_check` defaults to dry-run; a real teardown needs an
explicit `--live` opt-in AND credentials the repo never holds (GitHub secrets on the runner,
the macOS keychain on the Mac). The HTTP client below is never exercised in tests or dry-run.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from .core import DEFAULT_MAX_STALE_S, E2EClient, check_and_teardown

Opener = Callable[[str], str]


def _urllib_opener(url: str, *, timeout: float = 15.0) -> str:
    """Default network fetch. Imported lazily and used only for real http(s) sources — never in
    tests (which pass a fake opener) and never for a local file source."""
    import urllib.request

    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - fixed https source
        return r.read().decode("utf-8")


def load_remote_heartbeat(source, *, opener: Opener | None = None) -> dict | None:
    """Fetch the heartbeat JSON the run publishes off-box.

    `source` is either an http(s) URL (a gist raw URL, or raw.githubusercontent of a dedicated
    branch the run pushes to) or a local file path (a checked-out branch on the runner). Returns
    the parsed dict, or None on ANY failure — a watchdog must never crash on a bad or missing
    fetch, and a missing heartbeat is treated downstream as "unknown, do not delete".
    """
    s = str(source)
    try:
        if s.startswith(("http://", "https://")):
            body = (opener or _urllib_opener)(s)
        else:
            body = Path(s).read_text()
        d = json.loads(body)
    except Exception:  # noqa: BLE001 - any fetch/parse error is "no heartbeat", not a crash
        return None
    return d if isinstance(d, dict) and "ts" in d else None


def offbox_check(
    *,
    heartbeat_source,
    client: E2EClient | None,
    budget_cap_inr: float | None,
    max_hours: float | None = None,
    max_stale_s: float | None = DEFAULT_MAX_STALE_S,
    now: float | None = None,
    dry_run: bool = True,
    layer: str = "layer4-offbox",
    opener: Opener | None = None,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = lambda _m: None,
) -> dict:
    """Fetch the published heartbeat, then run the shared check+teardown on it.

    This is the off-box analogue of the on-box layers: it differs ONLY in *where* the heartbeat
    comes from (a URL instead of a local file). The decision (`decide`) and the delete
    (idempotent `check_and_teardown`) are the exact same shared code, so layer 4/5 can never
    disagree with layers 1-3. `dry_run` defaults to True here: an off-box run must opt in to act.
    """
    hb = load_remote_heartbeat(heartbeat_source, opener=opener)
    if hb is None:
        res = {"layer": layer, "source": str(heartbeat_source), "teardown": False,
               "reasons": [], "dry_run": dry_run, "deleted": None,
               "note": "no readable heartbeat at source; not tearing down"}
        log(json.dumps(res))
        return res
    # Reuse the ONE deterministic routine by writing the fetched beat to a temp file and handing
    # it the same path interface the on-box layers use. No decision logic is duplicated here.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "heartbeat.json"
        p.write_text(json.dumps(hb))
        res = check_and_teardown(
            heartbeat_path=p, client=client, budget_cap_inr=budget_cap_inr,
            max_hours=max_hours, max_stale_s=max_stale_s, now=now, dry_run=dry_run,
            layer=layer, sleep=sleep, log=log,
        )
    res["source"] = str(heartbeat_source)
    return res


# --------------------------------------------------------------------------- live HTTP client


class HttpE2EClient:
    """Live E2E delete client for an off-box runner, built ONLY for a real (non-dry-run)
    teardown. Mirrors the control host's delete path
    (hackathon/e2e-glimmer-test/ctl.py -> `post.call`): GET/DELETE `gpu/notebooks/{id}/` under a
    project, credentials from the runner's environment, never from the repo.

    The exact auth scheme (query apikey vs Bearer token) must be confirmed against the control
    host's `post.call` before this is enabled; it is UNVERIFIED here and this client is never
    exercised in tests or in dry-run. `exists()` returns False on a 404 so a delete is idempotent.
    """

    def __init__(self, vm_id, *, api_key, auth_token=None, project_id, location,
                 base="https://api.e2enetworks.com/myaccount/api/v1/", opener=None):
        self.vm_id = vm_id
        self.api_key = api_key
        self.auth_token = auth_token
        self.project_id = project_id
        self.location = location
        self.base = base.rstrip("/") + "/"
        self._opener = opener  # injectable transport; None -> urllib (only built for a real run)

    def _request(self, method: str, path: str):
        import urllib.error
        import urllib.parse
        import urllib.request

        q = urllib.parse.urlencode(
            {"apikey": self.api_key, "project_id": self.project_id, "location": self.location})
        url = f"{self.base}{path}?{q}"
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        req = urllib.request.Request(url, method=method, headers=headers)
        opener = self._opener or (lambda r: urllib.request.urlopen(r, timeout=30))  # noqa: S310
        try:
            resp = opener(req)
            code = getattr(resp, "status", 200)
            return code
        except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
            return e.code

    def exists(self) -> bool:
        return self._request("GET", f"gpu/notebooks/{self.vm_id}/") != 404

    def delete(self) -> None:
        self._request("DELETE", f"gpu/notebooks/{self.vm_id}/")


def build_live_client_from_env(env: dict | None = None) -> HttpE2EClient:
    """Build the live client from the runner's environment, or raise with a clear message.

    Reads E2E_API_KEY plus E2E_AUTH_TOKEN (or E2E_API_TOKEN), the VM id (E2E_VM_ID) and the
    project (E2E_PROJECT_ID / E2E_LOCATION). Called ONLY on the `--live` path, never in tests or
    dry-run. Refuses rather than pretending a VM could be deleted when anything is missing.
    """
    env = os.environ if env is None else env
    api_key = env.get("E2E_API_KEY")
    auth_token = env.get("E2E_AUTH_TOKEN") or env.get("E2E_API_TOKEN")
    vm_id = env.get("E2E_VM_ID")
    missing = [n for n, v in (("E2E_API_KEY", api_key), ("E2E_VM_ID", vm_id)) if not v]
    if missing:
        raise SystemExit(
            "live teardown needs credentials the repo never holds: missing "
            + ", ".join(missing)
            + ". Set them on the runner (GitHub secrets / keychain / env) or run --dry-run. "
            "This watchdog will never launch or delete a VM from a plain repo checkout."
        )
    return HttpE2EClient(
        vm_id, api_key=api_key, auth_token=auth_token,
        project_id=env.get("E2E_PROJECT_ID"), location=env.get("E2E_LOCATION", "Delhi"))


# --------------------------------------------------------------------------- CLI


def _optfloat(s):
    """CI passes unset variables as empty strings; treat ""/"unset" as None (not an error)."""
    if s is None:
        return None
    s = str(s).strip()
    return None if s == "" or s.lower() == "unset" else float(s)


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="wattnot.longrun.watchdog.offbox",
        description="Off-box (layer 4) watchdog: poll a published heartbeat, tear down on a cap.")
    ap.add_argument("--heartbeat-source", required=True,
                    help="http(s) URL (gist/raw branch) or local file path of the heartbeat JSON")
    # Caps are DEFAULT-ON backstops (same values as scripts/mac_watchdog) so an off-box run is
    # bounded even when the published heartbeat carries no deadline/spend of its own. Sized for the
    # 3.5 h H100 test (3.5 h x Rs451.2 = Rs1579, under Rs2000); raise them for a longer run. Config
    # values, not measurements. Pass "unset" (or "") to DISABLE a cap.
    ap.add_argument("--budget-cap-inr", default="2000",
                    help="off-box spend ceiling in INR (default-on, 2000; the 3.5 h test costs at most ~1579). "
                         "Pass 'unset' to disable.")
    ap.add_argument("--max-hours", default="3.6",
                    help="time-cap backstop in hours (default-on, 3.6; the 3.5 h test plus 6 min, behind ctl.py's delete at 3.5 h - 8 min). "
                         "Pass 'unset' to disable.")
    ap.add_argument("--max-stale-s", default=str(DEFAULT_MAX_STALE_S))
    ap.add_argument("--layer", default="layer4-offbox")
    ap.add_argument("--live", action="store_true",
                    help="actually delete on a cap (needs credentials); default is dry-run")
    a = ap.parse_args(argv)

    dry_run = not a.live
    client = None if dry_run else build_live_client_from_env()
    res = offbox_check(
        heartbeat_source=a.heartbeat_source, client=client,
        budget_cap_inr=_optfloat(a.budget_cap_inr), max_hours=_optfloat(a.max_hours),
        max_stale_s=_optfloat(a.max_stale_s), dry_run=dry_run, layer=a.layer,
    )
    print(json.dumps(res, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
