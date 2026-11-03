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
from wattnot.context_saver.middleware import conversation_tokens

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


def _only_done_call(response: dict, done_tool: str) -> str | None:
    """The reason text when the reply's only tool calls are the guard's `done` tool, else None.

    final-check mode treats such a reply as the agent's FINAL answer (r04e LangChain: 14 of 20
    runs ended on `done("Calculations completed...")`, which reached the caller as the answer and
    was never judged because it looked like a tool turn)."""
    try:
        msg = (response.get("choices") or [])[0].get("message") or {}
    except (IndexError, AttributeError):
        return None
    tcs = msg.get("tool_calls") or []
    if not tcs or any(((tc or {}).get("function") or {}).get("name") != done_tool for tc in tcs):
        return None
    raw = ((tcs[0] or {}).get("function") or {}).get("arguments") or ""
    try:
        return str(json.loads(raw).get("reason", raw)) if isinstance(raw, str) else str(raw)
    except (ValueError, AttributeError):
        return str(raw)


def _as_answer(response: dict, text: str) -> dict:
    """The reply rewritten as a plain final answer (no tool calls), same usage."""
    try:
        ch = dict((response.get("choices") or [])[0])
    except (IndexError, AttributeError):
        return response
    ch["message"] = {"role": "assistant", "content": text}
    ch["finish_reason"] = "stop"
    return {**response, "choices": [ch, *(response.get("choices") or [])[1:]]}


def _done_with_answer(tool: dict, done_tool: str) -> dict:
    fn = (tool or {}).get("function") or {}
    if fn.get("name") != done_tool:
        return tool
    desc = (str(fn.get("description") or "") + " Put your final answer in `reason`, in the format "
            "the task asks for (for math, end with '#### <number>').")
    return {**tool, "function": {**fn, "description": desc}}


FINALIZE_NOTE = ("Give your final answer now, in the format the task asks for (for math, the "
                 "final number on the last line as '#### <number>'). Do not call any tool.")


def finalize_body(body: dict, done_tool: str) -> dict:
    """One more call that must answer: no tools, the task's format restated."""
    out = {k: v for k, v in body.items() if k not in ("tools", "tool_choice")}
    out["messages"] = [*(body.get("messages") or []), {"role": "user", "content": FINALIZE_NOTE}]
    return out


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
    if tier.get("body_extra"):   # e.g. one served model with thinking off vs on
        out.update(tier["body_extra"])
    if tier.get("reasoning"):
        budget = int(tier.get("max_output") or REASONING_BUDGET)
        asked = out.pop("max_tokens", None) or out.get("max_completion_tokens") or 0
        out["max_completion_tokens"] = max(int(asked), budget)
    if tier.get("max_output_cap"):   # a hard ceiling on a step-up's thinking + answer
        cap = int(tier["max_output_cap"])
        for k in ("max_tokens", "max_completion_tokens"):
            if out.get(k):
                out[k] = min(int(out[k]), cap)
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
    final_check: bool = False,
    max_stepups: int = 1,
    short_bypass_tokens: int | None = None,
    waste_gate: Callable[[dict], bool] | None = None,
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
    * ``final_check`` (flag, off by default): agent mode. Tool-call turns are mid-task and never
      judged; only a FINAL answer (no tool calls) is checked by ``route.final_check`` and, when
      shaky, retried on the next tier. At most ``max_stepups`` step-ups per run id.
    * ``short_bypass_tokens``: short-conversation bypass. A call whose conversation is under this
      many (estimated) tokens has nothing for the saver to cut, so WattNot gets out of the way: no
      injected done tool, no answer-in-done instruction, no tier routing, and the final check
      only runs when the reply is empty. ``None`` = follow the context saver's own long-conversation
      gate (``min_tokens``, 500 by default) when a saver is on, else off; ``0`` = always off.
      Added after r04f-langchain (MEASURED: every call under 500 tokens, saver-on +21% energy).
    """
    from wattnot.route import final_check as fc
    from wattnot.route.escalate import gate as answer_gate

    cfg = config or WasteConfig()
    if short_bypass_tokens is None:
        gates = [int(getattr(mw, "min_tokens", 0) or 0)
                 for mw in getattr(context_saver, "middlewares", []) or []]
        short_bypass_tokens = max(gates) if gates else 0
    bypass_under = max(0, int(short_bypass_tokens))
    calls: list[dict] = []
    guards: OrderedDict[str, WasteGuard] = OrderedDict()
    stepups: dict[str, int] = {}
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
        msgs_in = body.get("messages") if isinstance(body.get("messages"), list) else []
        # only agent calls (a tools list) are bypassed; plain Q&A routing is unchanged
        short = (bool(bypass_under) and isinstance(body.get("tools"), list) and bool(body["tools"])
                 and conversation_tokens(msgs_in) < bypass_under)
        waste_on = waste_gate is None or waste_gate(body)   # per-model gate: off = budgets only, body untouched
        verdict = guard.before(body, inject_done=not short and waste_on)
        if (verdict.action == "stop" and final_check and verdict.reason
                and "budget" not in verdict.reason and isinstance(body.get("messages"), list)):
            # final-check mode: a detector stop (repeat, cycle, no-op...) must still end with an
            # answer, so the run gets one finalize call instead of a "stopped" text (r04e)
            fin_tier = (list(tiers) if tiers else [None])[-1]
            try:
                response = upstream(tier_body(finalize_body(body, cfg.done_tool), fin_tier),
                                    dict(request.headers))
            except Exception:  # noqa: BLE001
                response = None
            if response is not None:
                calls.append({"run": run[:16], "framework": detect_framework(request.headers),
                              "tokens_before": None, "tokens_after": None, "dropped_fields": dropped,
                              "stream": stream, "answered_by": None if fin_tier is None else fin_tier["name"],
                              "attempts": [{"tier": None if fin_tier is None else fin_tier["name"],
                                            "passed": None, "reason": f"finalize after guard stop: {verdict.reason}",
                                            "finalize": True}],
                              "gate_passed": True, "stopped": verdict.reason, "escalated_tokens": 0})
                guard.charge(response)
                return reply(response)
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
        out_body = verdict.body if waste_on else body
        if (final_check and not short and isinstance(out_body, dict)
                and isinstance(out_body.get("tools"), list)):
            # ask for the answer inside done(reason): a done() with the answer needs no finalize
            out_body = {**out_body, "tools": [_done_with_answer(t, cfg.done_tool) for t in out_body["tools"]]}
        rec: dict = {"run": run[:16], "framework": detect_framework(request.headers),
                     "tokens_before": None, "tokens_after": None,
               "dropped_fields": dropped, "stream": stream, "short_bypass": short}
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
                done_reason = _only_done_call(response, cfg.done_tool) if final_check and not short else None
                if done_reason is not None:
                    # `done` ends the run: judge it like a final answer. Tuned after the r04e
                    # LangChain run (13 of 14 step-ups were done() with no number, each paid a
                    # finalize on the thinking-on tier): first salvage an answer already in the
                    # reply or the reason (zero calls); else finalize on the CHEAPEST tier (a
                    # format fix, not a reasoning fix); the last tier only if that is still shaky,
                    # and never past the run's step-up cap.
                    k = request.headers.get("x-wattnot-kind") or fc.infer_kind(out_body.get("messages") or [])
                    msgs = out_body.get("messages") or []
                    ok, why = fc.check(k, done_reason, finish, msgs, response)
                    tname = None if tier is None else tier["name"]
                    if not ok and text and fc.check(k, text, finish, msgs, response)[0]:
                        response = _as_answer(response, text)
                        ok, why = True, "answer salvaged from the done reply's text"
                    if ok:
                        attempts.append({"tier": tname, "passed": True, "reason": f"done call judged: {why}"})
                        break
                    guard.charge(response)
                    u = response.get("usage") or {}
                    esc_tokens += int(u.get("prompt_tokens") or 0) + int(u.get("completion_tokens") or 0)
                    attempts.append({"tier": tname, "passed": False, "reason": f"done call judged: {why}"})
                    fin_chain = [chain[0]] + ([chain[-1]] if len(chain) > 1 else [])
                    for j, fin_tier in enumerate(fin_chain):
                        if j and stepups.get(run, 0) >= max_stepups:
                            attempts[-1]["reason"] += f"; step-up cap {max_stepups} reached, kept"
                            break
                        if j:
                            stepups[run] = stepups.get(run, 0) + 1
                            guard.charge(response)
                            u = response.get("usage") or {}
                            esc_tokens += int(u.get("prompt_tokens") or 0) + int(u.get("completion_tokens") or 0)
                        response = upstream(tier_body(finalize_body(out_body, cfg.done_tool), fin_tier), headers)
                        text, finish, tool = _answer(response)
                        ok2, why2 = fc.check(k, text, finish, msgs, response)
                        attempts.append({"tier": None if fin_tier is None else fin_tier["name"],
                                         "passed": ok2, "reason": f"finalize: {why2}", "finalize": True})
                        if ok2:
                            break
                    break
                if short and final_check and not (not tool and not (text or "").strip()):
                    # short-conversation bypass: keep the reply as is (an empty final answer
                    # still goes through the final check below)
                    attempts.append({"tier": None if tier is None else tier["name"],
                                     "passed": True, "reason": "short conversation: passed through"})
                    break
                if final_check:
                    if tool:   # mid-task: not a pass, not a fail, never stepped up
                        attempts.append({"tier": None if tier is None else tier["name"],
                                         "passed": None, "reason": "tool call (not judged)"})
                        break
                    k = request.headers.get("x-wattnot-kind") or fc.infer_kind(out_body.get("messages") or [])
                    ok, why = fc.check(k, text, finish, out_body.get("messages") or [], response)
                    if not ok and i < len(chain) - 1 and stepups.get(run, 0) >= max_stepups:
                        why = f"{why}; step-up cap {max_stepups} reached, kept"
                        attempts.append({"tier": None if tier is None else tier["name"],
                                         "passed": False, "reason": why})
                        break
                    if not ok and i < len(chain) - 1:
                        stepups[run] = stepups.get(run, 0) + 1
                elif tool:
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
        rec["gate_passed"] = bool(attempts and (attempts[-1]["passed"] or attempts[-1]["passed"] is None))
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
            "final_check": {"enabled": final_check, "max_stepups_per_run": max_stepups,
                            "tool_turns_not_judged": sum(1 for c in calls if c["attempts"]
                                                         and c["attempts"][0]["passed"] is None),
                            "final_answers": sum(1 for c in calls if c["attempts"]
                                                 and c["attempts"][0]["passed"] is not None),
                            "stepped_up": sum(1 for c in calls if len(c["attempts"]) > 1),
                            "reasons": {r: sum(1 for c in calls for a in c["attempts"][:1] if a["reason"] == r)
                                        for r in {a["reason"] for c in calls for a in c["attempts"][:1]
                                                  if a["passed"] is False}}},
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
