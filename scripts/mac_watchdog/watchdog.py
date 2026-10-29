#!/usr/bin/env python3
"""Layer 5: an off-box macOS watchdog for the long-run GPU VM. Stdlib only.

A Mac on a desk is a cheap, genuinely independent failure domain from the Linux control box:
if the control box loses power or network, this keeps polling and can still tear the VM down.
It polls the SAME heartbeat the run publishes (a URL, or a local file) and obeys the SAME rule
as every other layer: tear down on a budget cap, a time cap, or a stale heartbeat.

This file is standalone on purpose — you can scp just this folder to a Mac that has nothing but
the system python3. To avoid the rule drifting from the in-repo shared core
(`wattnot.longrun.watchdog.core.decide`), `tests/test_watchdog_offbox.py` asserts this `decide`
agrees with the shared one across a battery of inputs.

Safety:
  * `--dry-run` evaluates the rule and prints what it WOULD do, never calling the API.
  * The API key is read from the macOS keychain (`security find-generic-password`) or an env var,
    NEVER from the repo. A missing key on a live run is a hard refusal, not a silent no-op.
  * `delete()` is idempotent (checks existence first), so this racing the other layers is safe.

Keep the process awake with `caffeinate` (see INSTALL.md); the launchd plist and the tmux
wrapper both do this. Nothing here is installed or enabled by this repo.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

# Same measured E2E price the driver and the shared core use (H100 + GST + disk).
INR_PER_H = 451.2
# A run is "stale" if its heartbeat has not refreshed for this long (matches the shared core).
DEFAULT_MAX_STALE_S = 900.0


# --------------------------------------------------------------------------- the rule (mirrors core.decide)


def decide(*, now, deadline_ts, budget_spent_inr, budget_cap_inr, heartbeat_ts, max_stale_s):
    """Tear down if ANY cap is exceeded: budget, time, or a stale heartbeat. A None argument
    means that cap is not enforced. Pure: reads nothing, deletes nothing. Byte-for-byte the same
    logic as wattnot.longrun.watchdog.core.decide — kept in sync by a test."""
    reasons = []
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
        "budget_spent_inr": budget_spent_inr, "budget_cap_inr": budget_cap_inr,
        "deadline_ts": deadline_ts,
        "seconds_to_deadline": None if deadline_ts is None else round(deadline_ts - now, 1),
        "heartbeat_age_s": None if age is None else round(age, 1), "max_stale_s": max_stale_s,
    }
    return {"teardown": bool(reasons), "reasons": reasons, "detail": detail}


# --------------------------------------------------------------------------- heartbeat source


def load_heartbeat(source, *, opener: Callable[[str], str] | None = None) -> dict | None:
    """Fetch the heartbeat JSON. `source` is an http(s) URL or a local file path. Returns the
    parsed dict, or None on any failure (missing, unreachable, corrupt) — never raises."""
    s = str(source)
    try:
        if s.startswith(("http://", "https://")):
            if opener is not None:
                body = opener(s)
            else:
                with urllib.request.urlopen(s, timeout=15) as r:  # noqa: S310 - fixed https source
                    body = r.read().decode("utf-8")
        else:
            with open(s, encoding="utf-8") as f:
                body = f.read()
        d = json.loads(body)
    except Exception:  # noqa: BLE001 - any error is "no heartbeat", not a crash
        return None
    return d if isinstance(d, dict) and "ts" in d else None


# --------------------------------------------------------------------------- credentials (keychain / env)


def keychain_api_key(service: str, account: str,
                     runner: Callable[..., object] = subprocess.run) -> str | None:
    """Read a secret from the macOS keychain via `security find-generic-password -w`. Returns the
    password text, or None if it is not found. `runner` is injectable so tests never touch a real
    keychain."""
    try:
        out = runner(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True, text=True, timeout=10)
    except Exception:  # noqa: BLE001 - no `security` binary (not a Mac) -> treat as absent
        return None
    if getattr(out, "returncode", 1) != 0:
        return None
    pw = (getattr(out, "stdout", "") or "").strip()
    return pw or None


def resolve_credentials(env: dict, *, keychain: Callable[[str, str], str | None] | None = None,
                        service: str = "wattnot-e2e", account: str = "e2e-api-key") -> dict:
    """API key from the env (E2E_API_KEY) first, else the keychain; auth token from
    E2E_AUTH_TOKEN / E2E_API_TOKEN. Never from the repo. Returns {api_key, auth_token} with
    api_key possibly None (the caller refuses a live run without it)."""
    api_key = env.get("E2E_API_KEY")
    if not api_key and keychain is not None:
        api_key = keychain(service, account)
    return {"api_key": api_key, "auth_token": env.get("E2E_AUTH_TOKEN") or env.get("E2E_API_TOKEN")}


# --------------------------------------------------------------------------- live E2E client


class E2EHttpClient:
    """Live delete client. Mirrors the control host's delete path (GET/DELETE
    `gpu/notebooks/{id}/`). Built ONLY for a real teardown; never in tests or dry-run. The exact
    auth scheme must be confirmed against the control host's `post.call` before enabling."""

    def __init__(self, vm_id, *, api_key, auth_token, project_id, location,
                 base="https://api.e2enetworks.com/myaccount/api/v1/", opener=None):
        self.vm_id, self.api_key, self.auth_token = vm_id, api_key, auth_token
        self.project_id, self.location = project_id, location
        self.base = base.rstrip("/") + "/"
        self._opener = opener

    def _request(self, method, path):
        q = urllib.parse.urlencode(
            {"apikey": self.api_key, "project_id": self.project_id, "location": self.location})
        req = urllib.request.Request(f"{self.base}{path}?{q}", method=method,
                                     headers={"Content-Type": "application/json"})
        if self.auth_token:
            req.add_header("Authorization", f"Bearer {self.auth_token}")
        opener = self._opener or (lambda r: urllib.request.urlopen(r, timeout=30))  # noqa: S310
        try:
            return getattr(opener(req), "status", 200)
        except urllib.error.HTTPError as e:
            return e.code

    def exists(self) -> bool:
        return self._request("GET", f"gpu/notebooks/{self.vm_id}/") != 404

    def delete(self) -> None:
        self._request("DELETE", f"gpu/notebooks/{self.vm_id}/")


def teardown(client, *, tries=3, sleep=time.sleep, log=lambda m: None) -> dict:
    """Idempotent delete-and-verify. If the VM is already gone (another layer, or an earlier
    run), it reports success without deleting again. Safe to run from any layer, any number of
    times."""
    last_err = None
    for i in range(max(1, tries)):
        try:
            if not client.exists():
                return {"deleted": True, "already_gone": True, "attempts": i, "error": None}
            client.delete()
            if not client.exists():
                return {"deleted": True, "already_gone": False, "attempts": i + 1, "error": None}
        except Exception as e:  # noqa: BLE001 - a flaky API must not crash the watchdog
            last_err = f"{type(e).__name__}: {e}"
            log(f"teardown attempt {i + 1} failed: {last_err}")
        if i + 1 < tries:
            sleep(15.0)
    try:
        gone = not client.exists()
    except Exception:  # noqa: BLE001
        gone = False
    return {"deleted": gone, "already_gone": gone and last_err is None, "attempts": tries,
            "error": last_err}


# --------------------------------------------------------------------------- one check


def check_once(*, source, client, budget_cap_inr, max_hours=None,
               max_stale_s=DEFAULT_MAX_STALE_S, inr_per_h=INR_PER_H, now=None, dry_run=True,
               opener=None, delete_tries=3, sleep=time.sleep, log=lambda m: None) -> dict:
    """Fetch the heartbeat, apply the rule, and (unless dry-run) tear down. Mirrors the shared
    `check_and_teardown`. A missing heartbeat never tears down (unknown != dead)."""
    now = time.time() if now is None else now
    hb = load_heartbeat(source, opener=opener)
    if hb is None:
        res = {"layer": "layer5-mac", "source": str(source), "teardown": False, "reasons": [],
               "dry_run": dry_run, "deleted": None, "note": "no heartbeat; not tearing down"}
        log(json.dumps(res))
        return res

    deadline_ts = hb.get("deadline_ts")
    if deadline_ts is None and hb.get("launch_ts") is not None and max_hours is not None:
        deadline_ts = hb["launch_ts"] + max_hours * 3600.0
    spent = hb.get("spent_inr")
    spent_label = "MEASURED" if spent is not None else None
    if spent is None and hb.get("launch_ts") is not None:
        # No exact figure from the driver: derive an ESTIMATE from elapsed time x the measured
        # all-in rate. This is an ESTIMATE, never a metered spend, and is labelled as such below.
        spent = max(0.0, (now - hb["launch_ts"]) / 3600.0) * inr_per_h
        spent_label = "ESTIMATE (rate x time)"

    d = decide(now=now, deadline_ts=deadline_ts, budget_spent_inr=spent,
               budget_cap_inr=budget_cap_inr, heartbeat_ts=hb.get("ts"), max_stale_s=max_stale_s)
    res = {"layer": "layer5-mac", "source": str(source), "heartbeat": hb,
           "teardown": d["teardown"], "reasons": d["reasons"], "detail": d["detail"],
           "spent_inr_label": spent_label, "dry_run": dry_run, "deleted": None}
    if not d["teardown"]:
        log(json.dumps(res))
        return res
    if dry_run:
        res["deleted"] = "WOULD_DELETE (dry-run; no API call)"
        log(json.dumps(res))
        return res
    if client is None:
        res["deleted"] = False
        res["error"] = "no client for a live teardown"
        log(json.dumps(res))
        return res
    td = teardown(client, tries=delete_tries, sleep=sleep, log=log)
    res.update({"deleted": td["deleted"], "teardown_detail": td})
    log(json.dumps(res))
    return res


def run_loop(*, source, client, budget_cap_inr, max_hours=None,
             max_stale_s=DEFAULT_MAX_STALE_S, poll_every_s=120.0, dry_run=True,
             now: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
             opener=None, max_ticks=None, log=lambda m: None) -> dict:
    """Poll until a cap trips and the VM is torn down (or `max_ticks` is hit).

    `now` MUST be a wall-clock epoch clock (`time.time`): the rule compares it against the
    heartbeat's epoch timestamps (ts / launch_ts / deadline_ts), so a monotonic clock (which
    counts from an arbitrary origin) would make every age and deadline comparison meaningless.
    `time.monotonic` is only ever right for sleep pacing, never for these comparisons."""
    tick, last = 0, {}
    while True:
        tick += 1
        last = check_once(source=source, client=client, budget_cap_inr=budget_cap_inr,
                          max_hours=max_hours, max_stale_s=max_stale_s, now=now(),
                          dry_run=dry_run, opener=opener, sleep=sleep, log=log)
        if last["teardown"] and (dry_run or last.get("deleted") is True):
            return last
        if max_ticks is not None and tick >= max_ticks:
            return last
        sleep(poll_every_s)


# --------------------------------------------------------------------------- CLI


def _optfloat(s):
    if s is None:
        return None
    s = str(s).strip()
    return None if s == "" or s.lower() == "unset" else float(s)


def main(argv=None, *, env=None) -> int:
    import os

    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(description="Layer 5: macOS off-box long-run VM watchdog")
    ap.add_argument("--heartbeat-source", default=None,
                    help="http(s) URL or local file path. If omitted, defaults to the raw URL of "
                         "the dedicated branch the run publishes to (needs --repo-slug or "
                         "env WATCHDOG_PUBLISH_REPO).")
    ap.add_argument("--repo-slug", default=None,
                    help="'owner/name'; used to build the default heartbeat URL "
                         "(or env WATCHDOG_PUBLISH_REPO)")
    ap.add_argument("--heartbeat-branch", default=None,
                    help="branch holding the published heartbeat (default 'watchdog-heartbeat', "
                         "or env WATCHDOG_PUBLISH_BRANCH)")
    ap.add_argument("--budget-cap-inr", default=None)
    ap.add_argument("--max-hours", default=None)
    ap.add_argument("--max-stale-s", default=str(DEFAULT_MAX_STALE_S))
    ap.add_argument("--poll-every-s", type=float, default=120.0)
    ap.add_argument("--vm-id", default=None, help="E2E VM id (or env E2E_VM_ID)")
    ap.add_argument("--once", action="store_true", help="run a single check and exit")
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="evaluate the rule but never call the API (default)")
    ap.add_argument("--live", dest="dry_run", action="store_false",
                    help="actually delete on a cap (needs credentials)")
    a = ap.parse_args(argv)

    source = a.heartbeat_source
    if not source:
        slug = a.repo_slug or env.get("WATCHDOG_PUBLISH_REPO")
        branch = a.heartbeat_branch or env.get("WATCHDOG_PUBLISH_BRANCH") or "watchdog-heartbeat"
        if not slug:
            sys.stderr.write(
                "no --heartbeat-source and no --repo-slug / WATCHDOG_PUBLISH_REPO to build the "
                "default published-branch URL. Pass one.\n")
            return 2
        source = f"https://raw.githubusercontent.com/{slug}/{branch}/heartbeat.json"

    client = None
    if not a.dry_run:
        vm_id = a.vm_id or env.get("E2E_VM_ID")
        creds = resolve_credentials(env, keychain=keychain_api_key)
        if not creds["api_key"] or not vm_id:
            sys.stderr.write(
                "live teardown refused: need an E2E API key (keychain 'wattnot-e2e' or env "
                "E2E_API_KEY) and a VM id (--vm-id or E2E_VM_ID). Run --dry-run instead.\n")
            return 2
        client = E2EHttpClient(vm_id, api_key=creds["api_key"], auth_token=creds["auth_token"],
                               project_id=env.get("E2E_PROJECT_ID"),
                               location=env.get("E2E_LOCATION", "Delhi"))

    kw = dict(source=source, client=client,
              budget_cap_inr=_optfloat(a.budget_cap_inr), max_hours=_optfloat(a.max_hours),
              max_stale_s=_optfloat(a.max_stale_s), dry_run=a.dry_run, log=print)
    res = check_once(**kw) if a.once else run_loop(poll_every_s=a.poll_every_s, **kw)
    print(json.dumps(res, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
