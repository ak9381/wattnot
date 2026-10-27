from __future__ import annotations

import hashlib
import json
import urllib.request
from collections import OrderedDict
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from wattnot.agentwaste.config import WasteConfig
from wattnot.agentwaste.guard import WasteGuard

_COUNTER_KEYS = (
    "upstream_calls",
    "prompt_tokens",
    "completion_tokens",
    "reasoning_tokens",
    "nudges",
)
_FLAG_KINDS = ("repeat", "no_op", "cycle", "reasoning")


def _run_id(body: dict) -> str:
    for msg in body.get("messages", []) or []:
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content", "")
            text = content if isinstance(content, str) else json.dumps(content)
            return hashlib.sha256(text.encode()).hexdigest()
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def create_app(
    upstream: Callable[[dict, dict], dict],
    config: WasteConfig | None = None,
    max_runs: int = 256,
) -> FastAPI:
    """Build the proxy app. upstream(body, headers) returns parsed JSON."""
    cfg = config or WasteConfig()
    guards: OrderedDict[str, WasteGuard] = OrderedDict()
    app = FastAPI()

    def guard_for(run: str) -> WasteGuard:
        if run in guards:
            guards.move_to_end(run)
            return guards[run]
        guard = WasteGuard(cfg)
        guards[run] = guard
        while len(guards) > max_runs:
            guards.popitem(last=False)
        return guard

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse(
                status_code=400, content={"detail": "invalid JSON body"}
            )
        if not isinstance(body, dict):
            return JSONResponse(
                status_code=400, content={"detail": "invalid JSON body"}
            )
        if body.get("stream"):
            return JSONResponse(
                status_code=400,
                content={"detail": "streaming is not supported yet"},
            )
        run = request.headers.get("x-wattnot-run") or _run_id(body)
        guard = guard_for(run)
        verdict = guard.before(body)
        if verdict.action == "stop":
            assert verdict.reason is not None
            return JSONResponse(
                guard.stop_response(verdict.reason, model=str(body.get("model", "")))
            )
        try:
            response = upstream(verdict.body, dict(request.headers))
        except Exception:
            return JSONResponse(
                status_code=502, content={"detail": "upstream error"}
            )
        return JSONResponse(guard.after(response))

    @app.get("/v1/wattnot/stats")
    async def wattnot_stats() -> JSONResponse:
        runs = {run: guard.stats() for run, guard in guards.items()}
        total: dict = {key: 0 for key in _COUNTER_KEYS}
        total["flags"] = {kind: 0 for kind in _FLAG_KINDS}
        for stats in runs.values():
            for key in _COUNTER_KEYS:
                total[key] += int(stats.get(key, 0) or 0)
            for kind in _FLAG_KINDS:
                total["flags"][kind] += int(stats.get("flags", {}).get(kind, 0) or 0)
        return JSONResponse({"runs": runs, "total": total})

    return app


def http_upstream(base_url: str) -> Callable[[dict, dict], dict]:
    """Upstream callable that POSTs JSON. Forwards only Authorization."""

    url = base_url.rstrip("/") + "/v1/chat/completions"

    def upstream(body: dict, headers: dict) -> dict:
        auth = headers.get("authorization") or headers.get("Authorization")
        out_headers = {"Content-Type": "application/json"}
        if auth:
            out_headers["Authorization"] = auth
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), headers=out_headers
        )
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
            return json.loads(resp.read().decode())

    return upstream
