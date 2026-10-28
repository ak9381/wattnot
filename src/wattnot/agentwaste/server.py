from __future__ import annotations

import hashlib
import json
import urllib.request
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence

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


def detect_framework(headers) -> str:
    """Name the calling framework: explicit header first, then the User-Agent."""
    explicit = headers.get("x-wattnot-framework")
    if explicit:
        return explicit.strip().lower()[:40]
    ua = (headers.get("user-agent") or "").lower()
    for key, name in (("langchain", "langchain"), ("llama-index", "llamaindex"),
                      ("llamaindex", "llamaindex"), ("crewai", "crewai"), ("litellm", "litellm"),
                      ("openai/python", "openai-python")):
        if key in ua:
            return name
    return "unknown"


def _answer(response: dict) -> tuple[str | None, str | None, bool]:
    try:
        choice = (response.get("choices") or [])[0]
    except (IndexError, AttributeError):
        return None, None, False
    msg = choice.get("message") or {}
    return msg.get("content"), choice.get("finish_reason"), bool(msg.get("tool_calls"))


def create_app(
    upstream: Callable[[dict, dict], dict],
    config: WasteConfig | None = None,
    max_runs: int = 256,
    context_saver: object | None = None,
    tiers: Sequence[Mapping] | None = None,
    label: str = "MEASURED",
) -> FastAPI:
    """Build the proxy app. upstream(body, headers) returns parsed JSON.

    Optional layers, all on by passing them in:
    * ``context_saver``: trims repeated tool output and old turns before the call (token
      accounting is an ESTIMATE, ~4 chars/token).
    * ``tiers``: small-model-first routing. Each call goes to tier 0 first; the reference-free
      answer gate decides keep or escalate. A reply with tool calls counts as a pass (the agent
      is mid-task). The last tier always answers.
    Every call is logged per run with the framework name, so ``/v1/wattnot/receipt`` can say
    which framework drove it. ``label`` is MOCK or REPLAY when the upstream is not a real model.
    """
    from wattnot.route.escalate import gate as answer_gate

    cfg = config or WasteConfig()
    calls: list[dict] = []
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
        headers = dict(request.headers)
        out_body = verdict.body
        rec: dict = {"run": run[:16], "framework": detect_framework(request.headers),
                     "tokens_before": None, "tokens_after": None}
        if context_saver is not None and isinstance(out_body, dict) and out_body.get("messages"):
            saved, acct = context_saver.transform(list(out_body["messages"]))
            out_body = {**out_body, "messages": saved}
            rec["tokens_before"], rec["tokens_after"] = acct["tokens_before"], acct["tokens_after"]
        kind = request.headers.get("x-wattnot-kind", "qa")
        chain = list(tiers) if tiers else [None]
        attempts = []
        response: dict = {}
        try:
            for i, tier in enumerate(chain):
                body_t = out_body if tier is None else {**out_body, "model": tier["model"]}
                response = upstream(body_t, headers)
                text, finish, tool = _answer(response)
                ok, why = (True, "tool call") if tool else answer_gate(kind, text, finish)
                attempts.append({"tier": None if tier is None else tier["name"],
                                 "passed": ok, "reason": why})
                if ok or i == len(chain) - 1:
                    break
        except Exception:
            return JSONResponse(
                status_code=502, content={"detail": "upstream error"}
            )
        rec["attempts"] = attempts
        rec["answered_by"] = attempts[-1]["tier"] if attempts else None
        rec["gate_passed"] = bool(attempts and attempts[-1]["passed"])
        calls.append(rec)
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

    @app.get("/v1/wattnot/receipt")
    async def wattnot_receipt() -> JSONResponse:
        n = len(calls)
        routed = [c for c in calls if c["attempts"] and c["attempts"][0]["tier"] is not None]
        small_pass = sum(1 for c in routed if c["attempts"][0]["passed"])
        escalated = sum(1 for c in routed if len(c["attempts"]) > 1)
        tb = sum(c["tokens_before"] or 0 for c in calls)
        ta = sum(c["tokens_after"] or 0 for c in calls)
        waste = {key: 0 for key in _COUNTER_KEYS}
        stops = 0
        for g in guards.values():
            st = g.stats()
            for key in _COUNTER_KEYS:
                waste[key] += int(st.get(key, 0) or 0)
            stops += 1 if st.get("stopped") else 0
        frameworks = sorted({c["framework"] for c in calls})
        return JSONResponse({
            "spec": "wattnot.proxy-receipt/1",
            "label": label,
            "frameworks": frameworks,
            "framework": frameworks[0] if len(frameworks) == 1 else frameworks,
            "calls": n,
            "routing": {"enabled": bool(tiers), "routed_calls": len(routed),
                        "escalation_rate": round(escalated / len(routed), 4) if routed else None,
                        "small_model_pass_rate": round(small_pass / len(routed), 4) if routed else None,
                        "answered_by": {t: sum(1 for c in calls if c["answered_by"] == t)
                                        for t in {c["answered_by"] for c in calls}}},
            "gate": {"pass_pct": round(100 * sum(c["gate_passed"] for c in calls) / n, 2) if n else None},
            "context_saver": {"enabled": context_saver is not None, "tokens_before": tb,
                              "tokens_after": ta, "tokens_saved": tb - ta, "label": "ESTIMATE"},
            "agent_waste": {**waste, "runs": len(guards), "runs_stopped_early": stops},
        })

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
