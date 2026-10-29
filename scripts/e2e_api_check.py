#!/usr/bin/env python3
"""Read-only E2E API check: confirm auth works and a VM can be looked up. Stdlib only.

This script does the SAFE half of what the control host does: it authenticates and *reads*.
It issues ONLY read-only GET calls — list nodes, get a node by id, the account balance — and
has no code path that can create, modify, or delete anything. The HTTP method is hard-whitelisted
to GET (`_ALLOWED_METHODS`); any attempt to use another verb raises before a request is built.

    # dry-run (DEFAULT): prints the exact GETs it WOULD make, apikey redacted, no network call
    python scripts/e2e_api_check.py
    python scripts/e2e_api_check.py --vm-id 123456

    # live read-only check (needs E2E_API_KEY in the env): actually performs the GETs
    E2E_API_KEY=... E2E_PROJECT_ID=18993 E2E_LOCATION=Delhi \
        python scripts/e2e_api_check.py --live --vm-id 123456

Credentials come ONLY from the environment, never the repo:
  E2E_API_KEY        (required for --live)        -> apikey query param
  E2E_AUTH_TOKEN  or E2E_API_TOKEN  (optional)    -> Authorization: Bearer <token>
  E2E_PROJECT_ID, E2E_LOCATION                    -> project context query params

------------------------------------------------------------------------------------------------
THE DELETE CALL SHAPE (documented here on purpose; this script NEVER makes it)

The control host (hackathon/e2e-glimmer-test/ctl.py) deletes a VM with:

    DELETE {base}/gpu/notebooks/{id}/?apikey=...&project_id=...&location=...

i.e. the same path as `get_node(id)` below but with the HTTP verb DELETE instead of GET. To
confirm a delete SAFELY, without this script ever issuing one:
  1. Run this check (`--live --vm-id <id>`) and read the `status`/`id` back. Confirm the id is
     OUR VM and nothing else — never act on a VM you did not launch.
  2. A delete is confirmed done when a follow-up `GET gpu/notebooks/{id}/` returns HTTP 404 AND
     the VM's id no longer appears in `GET gpu/notebooks/` (the list). That is exactly the
     verification loop ctl.py's `delete()` runs; this script performs only the GET half of it,
     so you can confirm a VM's state before and after a delete made elsewhere.
The actual delete stays in ctl.py / the armed watchdog, behind their own go-ahead gates.
------------------------------------------------------------------------------------------------
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

BASE = "https://api.e2enetworks.com/myaccount/api/v1/"
# Hard whitelist. This script authenticates and reads; it can never mutate. Guarded at the one
# choke point (`_request`) so there is no way to smuggle a DELETE/POST through.
_ALLOWED_METHODS = ("GET",)


def _redact(url: str, api_key: str | None) -> str:
    """Hide the apikey in a URL so it is safe to print/log."""
    out = url
    if api_key:
        out = out.replace(urllib.parse.quote(api_key), "***").replace(api_key, "***")
    # Also mask any apikey=... query value regardless of how it was encoded.
    return urllib.parse.urlunparse(_mask_query(urllib.parse.urlparse(out)))


def _mask_query(parts):
    q = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    q = [(k, "***" if k == "apikey" else v) for k, v in q]
    return parts._replace(query=urllib.parse.urlencode(q))


class E2EReadOnly:
    """A read-only E2E client. `opener` is injectable so tests never touch the network; the
    default opener is built only on a live run."""

    def __init__(self, *, api_key: str | None, auth_token: str | None = None,
                 project_id: str | None = None, location: str | None = None,
                 base: str = BASE, opener: Callable | None = None) -> None:
        self.api_key = api_key
        self.auth_token = auth_token
        self.project_id = project_id
        self.location = location
        self.base = base.rstrip("/") + "/"
        self._opener = opener

    def _url(self, path: str, extra: dict | None = None) -> str:
        q = {"apikey": self.api_key or ""}
        if self.project_id:
            q["project_id"] = self.project_id
        if self.location:
            q["location"] = self.location
        if extra:
            q.update(extra)
        return f"{self.base}{path}?{urllib.parse.urlencode(q)}"

    def _request(self, method: str, path: str, extra: dict | None = None) -> tuple[int, object]:
        if method not in _ALLOWED_METHODS:
            raise ValueError(
                f"e2e_api_check is read-only: method {method!r} is not allowed "
                f"(only {_ALLOWED_METHODS}). It never creates, modifies, or deletes anything.")
        url = self._url(path, extra)
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        req = urllib.request.Request(url, method=method, headers=headers)
        opener = self._opener or (lambda r: urllib.request.urlopen(r, timeout=30))  # noqa: S310
        try:
            resp = opener(req)
            code = getattr(resp, "status", 200)
            body = resp.read().decode("utf-8") if hasattr(resp, "read") else ""
        except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
            return e.code, None
        try:
            return code, json.loads(body) if body else None
        except ValueError:
            return code, None

    # ----- the read-only endpoints (GET only) -----
    def verify_auth(self) -> tuple[int, object]:
        """GET customer/details/ — the lightest call that proves the apikey authenticates."""
        return self._request("GET", "customer/details/")

    def list_nodes(self) -> tuple[int, object]:
        """GET gpu/notebooks/ — list the VMs in the project (for the idempotency/name lookup)."""
        return self._request("GET", "gpu/notebooks/")

    def get_node(self, vm_id: str) -> tuple[int, object]:
        """GET gpu/notebooks/{id}/ — look a single VM up by id (same path a DELETE would use)."""
        return self._request("GET", f"gpu/notebooks/{vm_id}/")

    # ----- the plan a dry run prints (no network) -----
    def planned_requests(self, vm_id: str | None) -> list[dict]:
        reqs = [{"method": "GET", "what": "verify auth / balance",
                 "url": _redact(self._url("customer/details/"), self.api_key)},
                {"method": "GET", "what": "list nodes",
                 "url": _redact(self._url("gpu/notebooks/"), self.api_key)}]
        if vm_id:
            reqs.append({"method": "GET", "what": f"get node {vm_id}",
                         "url": _redact(self._url(f"gpu/notebooks/{vm_id}/"), self.api_key)})
        return reqs


def _balance_of(payload) -> float | None:
    try:
        return float((payload.get("data") or {}).get("prepaid_user_balance"))
    except (AttributeError, TypeError, ValueError):
        return None


def run_check(client: E2EReadOnly, *, vm_id: str | None, live: bool,
              log: Callable[[str], None] = print) -> dict:
    """Dry-run (default): print the exact GETs and make NO network call. Live: perform the GETs
    and summarise. Never issues anything but GET."""
    if not live:
        plan = client.planned_requests(vm_id)
        res = {"dry_run": True, "would_request": plan,
               "note": "dry-run: no network call made. Pass --live to actually run these GETs. "
                       "This script has no DELETE/POST path."}
        log(json.dumps(res, indent=2))
        return res

    res: dict = {"dry_run": False, "checks": {}}
    code, body = client.verify_auth()
    res["checks"]["auth"] = {"status": code, "ok": code == 200, "balance_inr": _balance_of(body)}
    code, body = client.list_nodes()
    ids = []
    if isinstance(body, dict):
        ids = [n.get("id") for n in (body.get("data") or []) if isinstance(n, dict)]
    res["checks"]["list_nodes"] = {"status": code, "ok": code == 200, "count": len(ids),
                                   "ids": ids}
    if vm_id:
        code, body = client.get_node(vm_id)
        d = (body.get("data") or {}) if isinstance(body, dict) else {}
        res["checks"]["get_node"] = {"status": code, "found": code == 200,
                                     "id": d.get("id"), "vm_status": d.get("status"),
                                     "is_ours": str(d.get("id")) == str(vm_id) if d else None}
    res["ok"] = all(c.get("ok", True) for c in res["checks"].values())
    log(json.dumps(res, indent=2))
    return res


def build_client(env: dict, *, opener: Callable | None = None) -> E2EReadOnly:
    return E2EReadOnly(
        api_key=env.get("E2E_API_KEY"),
        auth_token=env.get("E2E_AUTH_TOKEN") or env.get("E2E_API_TOKEN"),
        project_id=env.get("E2E_PROJECT_ID"),
        location=env.get("E2E_LOCATION", "Delhi"),
        opener=opener)


def main(argv=None, *, env=None, opener: Callable | None = None,
         log: Callable[[str], None] = print) -> int:
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(description="Read-only E2E auth + VM-lookup check (GET only).")
    ap.add_argument("--vm-id", default=None, help="also GET this VM by id (confirm it is ours)")
    ap.add_argument("--live", action="store_true",
                    help="actually perform the read-only GETs (default is dry-run, no network)")
    a = ap.parse_args(argv)

    client = build_client(env, opener=opener)
    if a.live and not client.api_key:
        sys.stderr.write("live check needs E2E_API_KEY in the environment (never the repo). "
                         "Run without --live for a dry-run that makes no network call.\n")
        return 2
    res = run_check(client, vm_id=a.vm_id, live=a.live, log=log)
    if a.live and not res.get("ok", False):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
