"""WattNot's agent decisions, made by Muse Spark through the spend guard.

Muse Spark only *proposes*. Every proposal is checked against an allow-list in code
and every change still has to pass the quality and p99 gates, so a bad answer costs a
few calls and never quality. What is sent: knob names, bounds, WattNot telemetry,
public workload prompts, and receipt JSON from fixtures. Nothing else.

Tasks:
  next_trial     propose the next knob setting to try, given the history so far
  effort_order   order the reasoning-effort levels to try per prompt class
  explain        explain a receipt in plain words and flag which numbers are ESTIMATE
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence

MODEL = "muse-spark-1.3"
EFFORTS = ("minimal", "low", "medium", "high")  # `high` is the reference (xhigh maps to it)

Client = Callable[..., dict]  # GuardedClient: client(body, purpose=...) -> response


def _body(system: str, payload: object, schema: dict, name: str, *, effort: str,
          max_tokens: int, model: str = MODEL) -> dict:
    return {"model": model, "reasoning_effort": effort, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": json.dumps(payload, separators=(",", ":"))}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": name, "schema": schema, "strict": True}}}


def usage_of(raw: Mapping) -> dict:
    u = raw.get("usage") or {}
    return {"input": int(u.get("prompt_tokens", 0)), "output": int(u.get("completion_tokens", 0)),
            "reasoning": int((u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0))}


def _json(raw: Mapping) -> dict:
    ch = raw["choices"][0]
    if not ch["message"].get("content"):  # ran out of tokens while reasoning
        raise ValueError(f"empty answer (finish_reason={ch.get('finish_reason')})")
    return json.loads(ch["message"]["content"])


# ---- next trial ----------------------------------------------------------------------------

TRIAL_SYSTEM = (
    "You tune a system for lower energy or cost per request without hurting quality or p99 "
    "latency. You get the knobs (each with its allowed values), the limits and the history of "
    "trials so far. Pick ONE next trial: kind=try with a knob and one of its allowed values, "
    "or kind=stop when more trials are unlikely to help. Never repeat a setting already tried. "
    "Prefer the change with the best expected saving that is still likely to pass the gates. "
    "The reason is one short line quoting the number that drove the choice. JSON only."
)

TRIAL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["kind", "knob", "value", "reason"],
    "properties": {"kind": {"enum": ["try", "stop"]}, "knob": {"type": ["string", "null"]},
                   "value": {"type": ["string", "number", "boolean", "null"]},
                   "reason": {"type": "string", "maxLength": 240}},
}


def next_trial(client: Client, scenario: Mapping, history: Sequence[Mapping]) -> dict:
    """-> {kind, knob, value, reason, usage}. Raises ValueError on an answer off the allow-list."""

    payload = {"goal": scenario["goal"], "knobs": scenario["knobs"], "limits": scenario["limits"],
               "trials": list(history)}
    raw = client(_body(TRIAL_SYSTEM, payload, TRIAL_SCHEMA, "NextTrial", effort="low",
                       max_tokens=4000), purpose=f"next-knob:{scenario['name']}")
    d = _json(raw)
    d["reason"] = " ".join(str(d.get("reason", "")).split())[:240]
    if d.get("kind") == "stop":
        return {**d, "knob": None, "value": None, "usage": usage_of(raw)}
    allowed = scenario["knobs"].get(d.get("knob"))
    if allowed is None or d.get("value") not in allowed:
        raise ValueError(f"off the allow-list: {d.get('knob')}={d.get('value')!r}")
    if any(h["setting"] == {d["knob"]: d["value"]} for h in history):
        raise ValueError("repeated a setting")
    return {**d, "usage": usage_of(raw)}


# ---- effort order --------------------------------------------------------------------------

ORDER_SYSTEM = (
    "You plan an experiment on a reasoning model. For each prompt class, give the order in "
    "which to try the reasoning effort levels, cheapest-likely-to-pass first. A level that "
    "passes the class's quality check ends the search for that class, so put levels you expect "
    "to pass early and skip none. Use only the levels provided. JSON only."
)


def order_schema(efforts: Sequence[str], classes: Sequence[str]) -> dict:
    one = {"type": "array", "items": {"enum": list(efforts)}}
    return {"type": "object", "additionalProperties": False, "required": ["orders", "note"],
            "properties": {"orders": {"type": "object", "additionalProperties": False,
                                      "required": list(classes),
                                      "properties": {c: one for c in classes}},
                           "note": {"type": "string", "maxLength": 240}}}


def effort_order(client: Client, classes: Mapping[str, Mapping],
                 efforts: Sequence[str] = EFFORTS[:-1]) -> dict:
    """classes: name -> {items, avg_prompt_chars, avg_answer_budget_tokens, ...}.

    -> {orders: {class: [effort...]}, note, usage}. Missing levels are appended in the
    default (cheapest-first) order, so every level is always reachable.
    """

    names = list(classes)
    payload = {"efforts": list(efforts), "classes": dict(classes)}
    raw = client(_body(ORDER_SYSTEM, payload, order_schema(efforts, names), "EffortOrder",
                       effort="medium", max_tokens=6000), purpose="effort-order")
    d = _json(raw)
    orders = {}
    for c in names:
        got = [e for e in (d.get("orders") or {}).get(c, []) if e in efforts]
        got = list(dict.fromkeys(got))
        orders[c] = got + [e for e in efforts if e not in got]
    return {"orders": orders, "note": str(d.get("note", ""))[:240], "usage": usage_of(raw)}


# ---- receipt notes -------------------------------------------------------------------------

EXPLAIN_SYSTEM = (
    "You explain a WattNot energy-saving receipt to an operator in plain words. Say what was "
    "changed, what was measured and what the saving is. Quote numbers exactly from the receipt. "
    "List every number that is an ESTIMATE or SIMULATED rather than MEASURED, and say if the "
    "saving is inside the noise. Do not add numbers that are not in the receipt. JSON only."
)

EXPLAIN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "measured", "estimated", "caveat"],
    "properties": {"summary": {"type": "string", "maxLength": 500},
                   "measured": {"type": "array", "items": {"type": "string"}},
                   "estimated": {"type": "array", "items": {"type": "string"}},
                   "caveat": {"type": "string", "maxLength": 300}},
}


def explain(client: Client, receipt: Mapping, name: str = "receipt") -> dict:
    raw = client(_body(EXPLAIN_SYSTEM, receipt, EXPLAIN_SCHEMA, "ReceiptNote", effort="low",
                       max_tokens=4000), purpose=f"explain-receipt:{name}")
    return {**_json(raw), "usage": usage_of(raw)}
