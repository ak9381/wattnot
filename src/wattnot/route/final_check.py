"""Final-answer check for agent effort routing (cheap setting first, step up when shaky).

Note: Glimmer ignores enable_thinking (smoke test 2026-11-03), so "thinking off" below is the
requested setting, not what the model did.

r04c on Modal: the LangChain saver arm answered 70 of 71 calls with thinking off and scored
11/20 against 19/20 without the saver, because every tool-calling reply counted as a pass and a
plain final answer only had to be non-empty. This module judges only the agent's FINAL answer
(a reply with no tool calls). Tool-call turns are never a pass or a fail; they are mid-task.

A final answer is shaky when any of these hold:
* malformed: math has no parseable ``#### <number>`` (or other final marker), code has no
  parseable Python block, or the reply is empty, a refusal, or cut off;
* low confidence: hedging words ("approximately", "I think", "not sure", ...) or, when the
  upstream returned logprobs, a mean token logprob below ``LOGPROB_FLOOR``;
* disagreement: math whose final number differs from the last calculator tool result in the
  conversation when the last turn before the answer was that tool's result.

A shaky answer is retried once on the thinking-on tier with the same messages. Step-ups are
capped per agent run (``max_stepups``), so cost stays bounded: at most one extra call per
step-up, and none once the run's cap is spent.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping, Sequence
from fractions import Fraction

from wattnot.gates.answers import _MARKERS, parse_number

HEDGE = re.compile(r"\b(approximately|roughly|about|i think|i believe|not sure|unsure|maybe|"
                   r"perhaps|probably|might be|could be|i guess|it seems|estimate)\b", re.IGNORECASE)
REFUSAL = re.compile(r"^\s*(i\s+(can(no|')t|am unable)|sorry)", re.IGNORECASE)
CODE_BLOCK = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)
LOGPROB_FLOOR = -0.6   # mean token logprob; below it the answer counts as low confidence
CALC_NAMES = ("calculator",)


def infer_kind(messages: Sequence[Mapping]) -> str:
    """math, code or qa from the first user message (header X-WattNot-Kind wins over this)."""
    for m in messages:
        if m.get("role") == "user":
            t = str(m.get("content") or "")
            if "def " in t or "```" in t or re.search(r"\bpython\b|\bfunction\b", t, re.I):
                return "code"
            if re.search(r"\d", t):
                return "math"
            return "qa"
    return "qa"


def _final_number(text: str) -> Fraction | None:
    for rx in _MARKERS:
        hits = rx.findall(text)
        if hits:
            return parse_number(hits[-1])
    return None


def _last_tool_result(messages: Sequence[Mapping]) -> tuple[str | None, str | None]:
    """(tool name, content) when the last message is a tool result, else (None, None)."""
    if not messages or messages[-1].get("role") != "tool":
        return None, None
    content = str(messages[-1].get("content") or "")
    tid = messages[-1].get("tool_call_id")
    for m in reversed(messages[:-1]):
        for tc in m.get("tool_calls") or []:
            if tc.get("id") == tid:
                return str((tc.get("function") or {}).get("name") or tc.get("name") or ""), content
    return None, content


def mean_logprob(response: Mapping) -> float | None:
    try:
        toks = response["choices"][0]["logprobs"]["content"]
        vals = [float(t["logprob"]) for t in toks if t.get("logprob") is not None]
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return sum(vals) / len(vals) if vals else None


def check(kind: str, text: str | None, finish: str | None, messages: Sequence[Mapping],
          response: Mapping | None = None) -> tuple[bool, str]:
    """(solid, reason) for one FINAL answer. solid=False means step up."""
    t = (text or "").strip()
    if not t:
        return False, "empty final answer"
    if finish == "length":
        return False, "final answer cut off"
    if REFUSAL.match(t):
        return False, "refusal"
    if kind == "math":
        n = _final_number(t)
        if n is None:
            return False, "malformed: no parseable final number"
        name, res = _last_tool_result(messages)
        if name in CALC_NAMES and res is not None:
            r = parse_number(res)
            if r is not None and r != n:
                return False, "disagreement: final number differs from the calculator result"
    elif kind == "code":
        m = CODE_BLOCK.search(t)
        src = m.group(1) if m else None
        if not src or "def " not in src:
            return False, "malformed: no Python function block"
        try:
            ast.parse(src)
        except SyntaxError:
            return False, "malformed: code does not parse"
    if HEDGE.search(t.splitlines()[-1] if kind == "math" else t[:400]):
        return False, "low confidence: hedged answer"
    lp = mean_logprob(response or {})
    if lp is not None and lp < LOGPROB_FLOOR:
        return False, f"low confidence: mean logprob {lp:.2f}"
    return True, "final answer looks solid"
