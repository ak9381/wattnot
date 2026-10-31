"""Graders for tiers-v1, plus the small agent loop the complex tier runs in."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field

from .tasks import TOOLS_DOC, Task

_NUM = re.compile(r"-?\d[\d,]*\.?\d*")


def last_number(text: str, marker: str | None = None) -> float | None:
    if marker and marker in text:
        text = text.rsplit(marker, 1)[1]
    found = _NUM.findall(text or "")
    if not found:
        return None
    try:
        return float(found[-1 if not marker else 0].replace(",", "").rstrip("."))
    except ValueError:
        return None


def same_number(got: float | None, want) -> bool:
    if got is None or want is None:
        return False
    want = float(want)
    return abs(got - want) <= max(0.011, abs(want) * 1e-4)


def code_block(text: str) -> str:
    m = re.findall(r"```(?:python)?\s*\n(.*?)```", text or "", re.S)
    return m[0] if m else (text or "")


def run_tests(code: str, tests: list[str], timeout_s: float = 5.0) -> bool:
    """Run model code + asserts in a separate, isolated Python with a timeout."""
    prog = code + "\n\n" + "\n".join(f"assert {t}" for t in tests) + "\n"
    try:
        p = subprocess.run([sys.executable, "-I", "-c", prog], capture_output=True,
                           timeout=timeout_s, text=True)
    except subprocess.TimeoutExpired:
        return False
    return p.returncode == 0


# ---- complex tier: tool world + agent loop -------------------------------------------------

@dataclass
class Trace:
    calls: list[tuple[str, dict]] = field(default_factory=list)
    errors: int = 0
    retries: int = 0
    subagents: int = 0
    turns: int = 0
    tokens: int = 0
    latencies: list[float] = field(default_factory=list)
    final: float | None = None
    stopped: str = ""


class World:
    def __init__(self, world: dict) -> None:
        self.w = world
        self.failed_once: set[str] = set()

    def call(self, tool: str, args: dict, trace: Trace):
        if tool in self.w.get("flaky", []) and tool not in self.failed_once:
            self.failed_once.add(tool)
            trace.errors += 1
            return "ERROR 503 service temporarily unavailable, try again"
        if tool in ("inventory", "price"):
            item = str(args.get("item", ""))
            return self.w[tool].get(item, f"ERROR unknown item {item!r}")
        if tool == "fx":
            to = str(args.get("to", "")).upper()
            if to not in self.w["fx"]:
                return f"ERROR unknown currency {to!r}"
            return round(float(args.get("amount", 0)) * self.w["fx"][to], 2)
        if tool == "calc":
            expr = str(args.get("expr", ""))
            if not re.fullmatch(r"[\d\s+\-*/().]+", expr) or len(expr) > 200:
                return "ERROR only numbers and + - * / ( ) are allowed"
            try:
                return round(eval(expr, {"__builtins__": {}}, {}), 6)  # noqa: S307 (filtered)
            except Exception as e:  # noqa: BLE001
                return f"ERROR {type(e).__name__}"
        return f"ERROR unknown tool {tool!r}"


_CALL = re.compile(r"^\s*CALL\s+(\w+)\s*(\{.*\})?\s*$", re.M)
_FINAL = re.compile(r"^\s*FINAL\s+(.+)$", re.M)


def _est_tokens(msgs: list[dict]) -> int:
    """Upper-side token estimate (3 chars per token + 8 per message for the template)."""
    return sum(len(m.get("content") or "") // 3 + 8 for m in msgs)


def run_agent(chat, task_text: str, world: World, trace: Trace, *, max_turns: int = 10,
              max_tokens: int = 192, depth: int = 0,
              context_limit: int | None = None) -> float | None:
    """`context_limit` (tokens, e.g. 8192 for Glimmer) stops the agent cleanly before the next
    turn could overflow the server's max_model_len: a fail ("context-limit"), not a vLLM error."""
    msgs = [{"role": "user", "content": task_text}]
    last_tool = None
    for _ in range(max_turns):
        if context_limit and _est_tokens(msgs) + max_tokens > context_limit:
            trace.stopped = "context-limit"
            return None
        r = chat(msgs, max_tokens)
        trace.turns += 1
        trace.tokens += r.tokens
        trace.latencies.append(r.latency_s)
        if r.finish == "error":
            trace.stopped = "client-error"
            return None
        msgs.append({"role": "assistant", "content": r.text})
        f = _FINAL.search(r.text or "")
        c = _CALL.search(r.text or "")
        if f and not c:
            return last_number(f.group(1))
        if not c:
            msgs.append({"role": "user", "content": "Reply with one CALL line or FINAL <number>."})
            continue
        tool = c.group(1)
        try:
            args = json.loads(c.group(2) or "{}")
        except json.JSONDecodeError:
            msgs.append({"role": "user", "content": "RESULT ERROR arguments must be JSON"})
            continue
        if tool == last_tool:
            trace.retries += 1
        last_tool = tool
        trace.calls.append((tool, args))
        if tool == "subagent":
            if depth >= 1:
                res = "ERROR sub-agents cannot start sub-agents"
            else:
                trace.subagents += 1
                sub = run_agent(chat, TOOLS_DOC + "\n\nTask: " + str(args.get("task", "")),
                                world, trace, max_turns=max_turns, max_tokens=max_tokens,
                                depth=depth + 1, context_limit=context_limit)
                res = "ERROR the sub-agent gave no answer" if sub is None else sub
        else:
            res = world.call(tool, args, trace)
        msgs.append({"role": "user", "content": f"RESULT {res}"})
    trace.stopped = trace.stopped or "turn-limit"
    return None


@dataclass
class Graded:
    id: str
    tier: str
    ok: bool
    tokens: int
    latency_s: float           # end-to-end for the item (sum of turns for agents)
    detail: dict = field(default_factory=dict)
    truncated: bool = False    # the model hit the token cap (finish=length / context-limit)


def grade_one(task: Task, chat) -> Graded:
    if task.tier == "complex":
        tr = Trace()
        got = run_agent(chat, task.prompt, World(task.world or {}), tr, max_tokens=task.max_tokens,
                        context_limit=getattr(chat, "context_limit", None))
        used = {c[0] for c in tr.calls}
        ok = (same_number(got, task.answer) and set(task.needs_tools) <= used
              and (not task.needs_subagent or tr.subagents > 0))
        return Graded(task.id, task.tier, ok, tr.tokens, sum(tr.latencies),
                      {"final": got, "tools": sorted(used), "turns": tr.turns,
                       "retries": tr.retries, "tool_errors": tr.errors,
                       "subagents": tr.subagents, "stopped": tr.stopped},
                      truncated=tr.stopped == "context-limit")
    r = chat([{"role": "user", "content": task.prompt}], task.max_tokens)
    if r.finish == "error":
        return Graded(task.id, task.tier, False, 0, r.latency_s, {"error": True})
    if task.tier == "hard":
        ok = run_tests(code_block(r.text), task.tests)
    else:
        ok = same_number(last_number(r.text, "ANSWER:" if task.tier == "medium" else None),
                         task.answer)
    # finish == "length": the answer was cut off by the token cap, not necessarily wrong. Flag it
    # so the summary can report the truncation rate instead of silently counting it as a miss.
    return Graded(task.id, task.tier, ok, r.tokens, r.latency_s, {"finish": r.finish},
                  truncated=r.finish == "length")
