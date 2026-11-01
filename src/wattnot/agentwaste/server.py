from __future__ import annotations

import hashlib
import json
import urllib.request
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from wattnot.agentwaste.config import WasteConfig
from wattnot.agentwaste.guard import WasteGuard

_COUNTER_KEYS = (
    "upstream_calls",
    "prompt_tokens",
    "completion_tokens",
    "reasoning_tokens",
    "nudges",
)
_FLAG_KINDS = ("repeat", "no_op", "cycle", "reasoning", "error_storm", "low_gain", "paraphrase")


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


# OpenAI chat fields the proxy forwards upstream. Anything else is dropped (and listed in the
# receipt) so a framework's extras never turn into an upstream 400.
ALLOWED_FIELDS = frozenset({
    "model", "messages", "tools", "tool_choice", "temperature", "top_p", "max_tokens",
    "max_completion_tokens", "stop", "response_format", "seed", "frequency_penalty",
    "presence_penalty", "reasoning_effort", "user", "metadata",
})
# Handled by the proxy itself, never forwarded.
PROXY_FIELDS = frozenset({"stream", "stream_options", "n"})
REASONING_BUDGET = 2000  # default output budget for a tier marked reasoning=True


def sanitize(body: dict) -> tuple[dict, list[str], str | None]:
    """(clean body, dropped field names, error). n > 1 is an error, not silently dropped."""
    n = body.get("n")
    if n not in (None, 1):
        return body, [], "n > 1 is not supported"
    clean = {k: v for k, v in body.items() if k in ALLOWED_FIELDS}
    dropped = sorted(k for k in body if k not in ALLOWED_FIELDS and k not in PROXY_FIELDS)
    return clean, dropped, None


def tier_body(body: dict, tier: Mapping | None) -> dict:
    """Body for one tier: its model, and for reasoning tiers a larger output budget."""
    if tier is None:
        return body
    out = {**body, "model": tier["model"]}
    if tier.get("reasoning"):
        budget = int(tier.get("max_output") or REASONING_BUDGET)
        asked = out.pop("max_tokens", None) or out.get("max_completion_tokens") or 0
        out["max_completion_tokens"] = max(int(asked), budget)
    return out


def reasoning_cutoff(response: dict, text: str | None, finish: str | None) -> bool:
    """True when finish=length but a visible answer exists and reasoning used most of the
    output budget: the cut-off came from thinking, not from a truncated answer."""
    if finish != "length" or not (text or "").strip():
        return False
    usage = response.get("usage") or {}
    comp = int(usage.get("completion_tokens") or 0)
    reas = int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
    return comp > 0 and reas >= 0.5 * comp


def sse_chunks(response: dict, include_usage: bool):
    """Replay one buffered completion as OpenAI SSE chunks."""
    cid = response.get("id", "wattnot")
    model = response.get("model", "")
    created = response.get("created", 0)
    choice = (response.get("choices") or [{}])[0]
    msg = choice.get("message") or {}

    def chunk(delta, finish=None, usage=None):
        c = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
             "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        if usage is not None:
            c = {**c, "choices": [], "usage": usage}
        return "data: " + json.dumps(c) + "\n\n"

    yield chunk({"role": "assistant", "content": ""})
    content = msg.get("content") or ""
    for i in range(0, len(content), 64):
        yield chunk({"content": content[i:i + 64]})
    tcs = msg.get("tool_calls") or []
    if tcs:
        yield chunk({"tool_calls": [{**tc, "index": i} for i, tc in enumerate(tcs)]})
    fin = chunk({}, choice.get("finish_reason") or "stop")
    if response.get("wattnot"):
        # keep the done/stopped envelope for streaming clients (extra key, ignored by SDKs)
        c = json.loads(fin[len("data: "):])
        c["wattnot"] = response["wattnot"]
        fin = "data: " + json.dumps(c) + "\n\n"
    yield fin
    if include_usage and response.get("usage"):
        yield chunk({}, usage=response["usage"])
    yield "data: [DONE]\n\n"


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
        stream = bool(body.get("stream"))
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
        body, dropped, err = sanitize(body)
        if err:
            return JSONResponse(status_code=400, content={"detail": err})

        def reply(payload: dict):
            if stream:
                return StreamingResponse(sse_chunks(payload, include_usage),
                                         media_type="text/event-stream")
            return JSONResponse(payload)
        run = request.headers.get("x-wattnot-run") or _run_id(body)
        guard = guard_for(run)
        verdict = guard.before(body)
        if verdict.action == "stop":
            assert verdict.reason is not None
            # a guard stop is a call too: count it, so the receipt's call total is honest
            calls.append({"run": run[:16], "framework": detect_framework(request.headers),
                          "tokens_before": None, "tokens_after": None,
                          "dropped_fields": dropped, "stream": stream, "attempts": [],
                          "answered_by": "wattnot:guard", "gate_passed": False,
                          "stopped": verdict.reason, "escalated_tokens": 0})
            return reply(
                guard.stop_response(verdict.reason, model=str(body.get("model", "")))
            )
        headers = dict(request.headers)
        out_body = verdict.body
        rec: dict = {"run": run[:16], "framework": detect_framework(request.headers),
                     "tokens_before": None, "tokens_after": None,
               "dropped_fields": dropped, "stream": stream}
        if context_saver is not None and isinstance(out_body, dict) and out_body.get("messages"):
            saved, acct = context_saver.transform(list(out_body["messages"]))
            out_body = {**out_body, "messages": saved}
            rec["tokens_before"], rec["tokens_after"] = acct["tokens_before"], acct["tokens_after"]
        kind = request.headers.get("x-wattnot-kind", "qa")
        chain = list(tiers) if tiers else [None]
        attempts = []
        response: dict = {}
        esc_tokens = 0
        try:
            for i, tier in enumerate(chain):
                body_t = tier_body(out_body, tier)
                response = upstream(body_t, headers)
                text, finish, tool = _answer(response)
                if tool:
                    ok, why = True, "tool call"
                elif reasoning_cutoff(response, text, finish):
                    ok, why = answer_gate(kind, text, None)
                    why = f"{why} (length cut-off from reasoning ignored)"
                else:
                    ok, why = answer_gate(kind, text, finish)
                attempts.append({"tier": None if tier is None else tier["name"],
                                 "passed": ok, "reason": why})
                if ok or i == len(chain) - 1:
                    break
                guard.charge(response)  # escalated attempt: billed, never shown to the caller
                u = response.get("usage") or {}
                esc_tokens += int(u.get("prompt_tokens") or 0) + int(u.get("completion_tokens") or 0)
        except Exception:
            # keep a record: tokens already charged for escalated attempts stay visible in
            # /stats and the receipt, and the call counts as an upstream error, not a pass
            rec.update({"attempts": attempts, "answered_by": "wattnot:upstream_error",
                        "gate_passed": False, "error": "upstream error",
                        "escalated_tokens": esc_tokens})
            calls.append(rec)
            return JSONResponse(
                status_code=502, content={"detail": "upstream error"}
            )
        rec["attempts"] = attempts
        rec["answered_by"] = attempts[-1]["tier"] if attempts else None
        rec["gate_passed"] = bool(attempts and attempts[-1]["passed"])
        rec["escalated_tokens"] = esc_tokens
        calls.append(rec)
        out = guard.after(response)
        if esc_tokens and isinstance(out, dict):
            # `usage` stays the answer's own (OpenAI shape); the hidden escalated attempts are
            # billed too, so say how many tokens they cost
            env = dict(out.get("wattnot") or {})
            env["escalated_attempt_tokens"] = esc_tokens
            out = {**out, "wattnot": env}
        return reply(out)

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
        answered = [c for c in calls if not c.get("stopped") and not c.get("error")]
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
            "gate": {"pass_pct": round(100 * sum(c["gate_passed"] for c in answered) / len(answered), 2)
                     if answered else None, "answered_calls": len(answered),
                     "stopped_by_guard": sum(1 for c in calls if c.get("stopped")),
                     "upstream_errors": sum(1 for c in calls if c.get("error"))},
            "escalated_attempt_tokens": sum(c.get("escalated_tokens", 0) for c in calls),
            "context_saver": {"enabled": context_saver is not None, "tokens_before": tb,
                              "tokens_after": ta, "tokens_saved": tb - ta, "label": "ESTIMATE"},
            "agent_waste": {**waste, "runs": len(guards), "runs_stopped_early": stops},
            "billed_upstream_calls": waste["upstream_calls"],
            "streamed_calls": sum(1 for c in calls if c.get("stream")),
            "dropped_fields": sorted({f for c in calls for f in c.get("dropped_fields", [])}),
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
