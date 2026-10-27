"""A small local HTTP/JSON API over the proposal store, for the future frontend.

    wattnot proposals serve [--host 127.0.0.1] [--port 8781]

It is meant for this machine only. Reading is open on the loopback address; anything that
changes state (approve, reject, apply, rollback) needs `Authorization: Bearer <token>`. The
token is `$WATTNOT_API_TOKEN`, or a random one printed once at start-up. The auto-approve
policy can be read here but never changed here; it is a file a person edits.

Routes (all JSON, errors are {"code": ..., "message": ...}):

    GET  /v1/health
    GET  /v1/proposals?status=&kind=
    GET  /v1/proposals/{id}                    the proposal plus its audit trail
    POST /v1/proposals/{id}/approve            {"by", "note", "sha256", "apply"}
    POST /v1/proposals/{id}/reject             {"by", "note"}
    POST /v1/proposals/{id}/apply              {"by"}
    POST /v1/proposals/{id}/rollback           {"by", "note"}
    GET  /v1/audit?proposal_id=
    GET  /v1/audit/verify
    GET  /v1/policy
"""

from __future__ import annotations

import hmac
import os
import secrets
import sys
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from wattnot.approvals import model as m
from wattnot.approvals import policy as pol
from wattnot.approvals.appliers import ApplyError
from wattnot.approvals.service import HTTP, Approvals, StateError
from wattnot.approvals.store import NotFound

LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1", "testserver")
DEFAULT_CORS = ("http://localhost:3000", "http://127.0.0.1:3000",
                "http://localhost:5173", "http://127.0.0.1:5173")


def _err(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"code": code, "message": message}, status_code=status)


def create_app(approvals: Approvals, *, token: str | None = None,
               cors_origins: Sequence[str] = DEFAULT_CORS,
               allowed_hosts: Sequence[str] = LOCAL_HOSTS) -> FastAPI:
    """Build the app. `token` is required for every POST."""
    if not token:
        raise ValueError("an API token is required")
    app = FastAPI(title="WattNot approvals API", version="1")
    app.add_middleware(CORSMiddleware, allow_origins=list(cors_origins),
                       allow_methods=["GET", "POST"],
                       allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def _host_check(request: Request, call_next):  # DNS-rebinding guard
        host = (request.headers.get("host") or "").rsplit(":", 1)[0] \
            if not (request.headers.get("host") or "").startswith("[") \
            else (request.headers.get("host") or "").split("]")[0] + "]"
        if host not in allowed_hosts:
            return _err("bad_host", f"host {host!r} is not allowed", 400)
        return await call_next(request)

    def _need_token(request: Request) -> JSONResponse | None:
        got = request.headers.get("authorization", "")
        if not got.lower().startswith("bearer ") or not hmac.compare_digest(
                got[7:].strip().encode(), token.encode()):
            return _err("unauthorized", "send Authorization: Bearer <token>", 401)
        return None

    def _guard(fn):
        try:
            return fn()
        except NotFound:
            return _err("not_found", "no such proposal", 404)
        except StateError as e:
            return _err("conflict", str(e), 409)
        except ApplyError as e:
            return _err("apply_failed", str(e), 409)
        except (m.ProposalError, ValueError) as e:
            return _err("bad_request", str(e), 400)

    async def _body(request: Request) -> dict:
        try:
            b = await request.json()
        except ValueError:
            return {}
        return b if isinstance(b, dict) else {}

    def _by(b: dict) -> str:
        by = str(b.get("by") or "").strip()
        return by[:80] if by else m.default_actor()

    def _view(pid: str) -> dict:
        return {**approvals.get(pid), "audit": approvals.audit(pid)}

    @app.get("/v1/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "folder": str(approvals.store.root),
                "pending": len(approvals.list(m.PENDING))}

    @app.get("/v1/proposals")
    def proposals(status: str | None = None, kind: str | None = None):
        if status and status not in m.STATUSES or kind and kind not in m.KINDS:
            return _err("bad_request", "unknown status or kind", 400)
        return approvals.list(status, kind)

    @app.get("/v1/proposals/{pid}")
    def one(pid: str):
        return _guard(lambda: _view(pid))

    @app.post("/v1/proposals/{pid}/approve")
    async def approve(pid: str, request: Request):
        if (bad := _need_token(request)) is not None:
            return bad
        b = await _body(request)

        def go():
            approvals.approve(pid, _by(b), via=HTTP, note=str(b.get("note") or "")[:500],
                              expect_sha256=b.get("sha256"))
            if b.get("apply"):
                approvals.apply(pid, by=_by(b), via=HTTP)
            return _view(pid)
        return _guard(go)

    @app.post("/v1/proposals/{pid}/reject")
    async def reject(pid: str, request: Request):
        if (bad := _need_token(request)) is not None:
            return bad
        b = await _body(request)
        return _guard(lambda: (approvals.reject(pid, _by(b), via=HTTP,
                                                note=str(b.get("note") or "")[:500]),
                               _view(pid))[1])

    @app.post("/v1/proposals/{pid}/apply")
    async def apply(pid: str, request: Request):
        if (bad := _need_token(request)) is not None:
            return bad
        b = await _body(request)
        return _guard(lambda: (approvals.apply(pid, by=_by(b), via=HTTP), _view(pid))[1])

    @app.post("/v1/proposals/{pid}/rollback")
    async def rollback(pid: str, request: Request):
        if (bad := _need_token(request)) is not None:
            return bad
        b = await _body(request)
        return _guard(lambda: (approvals.rollback(pid, _by(b), via=HTTP,
                                                  note=str(b.get("note") or "")[:500]),
                               _view(pid))[1])

    @app.get("/v1/audit")
    def audit(proposal_id: str | None = None):
        return approvals.audit(proposal_id)

    @app.get("/v1/audit/verify")
    def verify():
        problems = approvals.verify()
        return {"ok": not problems, "entries": len(approvals.audit()), "problems": problems}

    @app.get("/v1/policy")
    def policy():
        try:
            p = approvals.policy()
        except pol.PolicyError as e:
            return _err("bad_policy", str(e), 500)
        return {**p.to_dict(), "sha256": p.sha256(), "editable_here": False}

    return app


def serve(approvals: Approvals, *, host: str = "127.0.0.1", port: int = 8781) -> int:
    import uvicorn

    token = os.environ.get("WATTNOT_API_TOKEN") or secrets.token_urlsafe(24)
    generated = not os.environ.get("WATTNOT_API_TOKEN")
    allowed = LOCAL_HOSTS if host in ("127.0.0.1", "localhost", "::1") else (*LOCAL_HOSTS, host)
    print(f"WattNot approvals API on http://{host}:{port} (folder {approvals.store.root})",
          flush=True)
    if generated:
        print(f"token for POST requests (shown once): {token}", flush=True)
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("warning: this is not a loopback address; the API is for this machine only",
              file=sys.stderr, flush=True)
    uvicorn.run(create_app(approvals, token=token, allowed_hosts=allowed), host=host,
                port=port, log_level="warning")
    return 0
