"""Three small agent-harness loops, with and without the saver behind them.

These are our own minimal versions of three common loop patterns. They are NOT the real
libraries; each one copies only the stop rule and the iteration cap of its pattern:

  react     A tool-calling "ReAct" loop with `max_iterations` (same stop rule as LangChain's
            AgentExecutor: the run ends when the model answers without a tool call; cap 15).
  stop      A conversation loop that ends when the reply carries a stop marker (`TASK_DONE`) or a
            JSON final answer, else the harness says "Continue." (same idea as AutoGen's
            `is_termination_msg` with `max_consecutive_auto_reply`; cap 10).
  planner   A planner that hands sub-tasks to helper agents through a `delegate` tool (the
            "agents as tools" / supervisor pattern of LangGraph, CrewAI and the OpenAI Agents
            SDK). The planner has its own iteration cap (10); each helper has its own (6).

Every loop keeps its own stop rule and cap switched on. The saver is a second layer: the
loop's model client is wrapped in `WasteProxy`, and the loop checks the saver's `wattnot`
field on each reply (`done` or `stopped`), about three lines per loop. For the planner, every
agent shares one `SharedBudget`.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from wattnot.agentwaste.config import WasteConfig
from wattnot.agentwaste.guard import HELPER_STOPPED, SharedBudget
from wattnot.agentwaste.proxy import WasteProxy

from .env import MUTATING, TOOLS, World
from .runner import MODEL, _args, _Counting

REACT_CAP = 15
STOP_CAP = 10
PLANNER_CAP = 10
HELPER_CAP = 6
SHARED_CALLS = 12       # one budget for a planner and all its helpers (saver arm only)
SAFETY_CALLS = 60       # the bench's own emergency stop, on for both arms, never the saver's
MARKER = "TASK_DONE"
CONTINUE = "Continue."

SYS_REACT = ("You are an operations agent. Use the tools to finish the assignment, report with "
             "post_message, then reply with a short final answer. Be concise.")
SYS_STOP = ("You are an operations agent working in a loop. Use the tools to finish the "
            "assignment and report with post_message. When the assignment is finished, reply "
            f"with the word {MARKER} followed by a one-line summary. Be concise.")
SYS_PLANNER = ("You are a planning agent. Use the tools yourself, or hand a self-contained "
               "sub-task to a helper with delegate(task); the helper has the same tools and "
               "returns a short report. Report results with post_message, then reply with a "
               "short final answer. Be concise.")
SYS_HELPER = ("You are a helper agent. Do the sub-task you are given with the tools, then reply "
              "with a short report. Be concise.")

DELEGATE = {"type": "function", "function": {
    "name": "delegate", "description": "Hand a self-contained sub-task to a helper agent. "
    "Returns the helper's short report.",
    "parameters": {"type": "object", "properties": {"task": {"type": "string"}},
                   "required": ["task"]}}}

Client = Callable[..., dict]


class SafetyStop(RuntimeError):
    """The bench's own emergency stop (not the saver's): too many model calls in one run."""


@dataclass
class HRun:
    harness: str
    case: str
    category: str
    arm: str                    # harness | harness+saver
    rep: int = 0
    success: bool = False
    calls: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    tool_calls: int = 0
    mutations: int = 0
    wall_s: float = 0.0
    ended_by: str = ""          # final | marker | cap | saver-done | saver-stop | safety | error
    miss: bool = False          # the harness's own stop rule did not end the run
    helpers: int = 0
    helper_caps: int = 0
    nudges: int = 0
    stop_reason: str | None = None
    flags: dict = field(default_factory=dict)
    error: str | None = None
    said: list[str] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def as_dict(self) -> dict:
        d = asdict(self)
        d["total_tokens"] = self.total_tokens
        return d


def _short(s: object, n: int) -> str:
    t = s if isinstance(s, str) else json.dumps(s, separators=(",", ":"))
    t = " ".join(t.split())
    return t if len(t) <= n else t[: n - 1] + "…"


def stop_rule_met(text: str, rule: str) -> bool:
    """The `stop` harness's own stop rule: contains | strict | json."""
    if rule == "contains":
        return MARKER in text
    if rule == "strict":
        return text.strip() == MARKER
    if rule == "json":
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return False
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            return False
        return isinstance(obj, dict) and "final_answer" in obj
    raise ValueError(f"unknown stop rule {rule}")


class Run:
    """State for one run: the world, the counting client, the saver switch and the trace."""

    def __init__(self, client: Client, world: World, *, saver: bool, agent_cap: int,
                 effort: str = "low", max_tokens: int = 1500, case_id: str = "",
                 quota: int | None = None, safety_calls: int = SAFETY_CALLS,
                 shared: bool = False, purpose: str = "agentwaste-v2") -> None:
        self.purpose = purpose
        self.world, self.saver, self.agent_cap = world, saver, agent_cap
        self.effort, self.max_tokens, self.case_id = effort, max_tokens, case_id
        self.quota, self.safety_calls = quota, safety_calls
        self.counting = _Counting(client)
        self.shared = SharedBudget(SHARED_CALLS, 60000, 4) if saver and shared else None
        self.proxies: list[WasteProxy] = []
        self.said: list[str] = []
        self.trace: list[dict] = []
        self.tool_calls = self.helpers = self.helper_caps = 0
        self.flags_seen: dict[str, int] = {}

    # one model client per agent: its own saver state, one shared budget
    def agent_call(self) -> Callable[..., dict]:
        def guarded(body, **kw):
            if self.counting.calls >= self.safety_calls:
                raise SafetyStop("bench safety stop")
            return self.counting(body, **kw)
        if not self.saver:
            return guarded
        p = WasteProxy(guarded, WasteConfig.second_layer(self.agent_cap), self.shared)
        self.proxies.append(p)
        return p

    def model(self, call, agent: str, msgs: list[dict], tools: list[dict]) -> tuple[dict, dict]:
        body = {"model": MODEL, "reasoning_effort": self.effort, "max_tokens": self.max_tokens,
                "temperature": 0, "tools": tools, "messages": msgs}
        before = self.counting.prompt + self.counting.completion
        r = call(body, purpose=f"{self.purpose}:{self.case_id}")
        msg = r["choices"][0]["message"]
        wn = r.get("wattnot") or {}
        ev = {"n": len(self.trace) + 1, "agent": agent,
              "text": _short(msg.get("content") or "", 150), "calls": [],
              "tokens": self.counting.prompt + self.counting.completion - before,
              "note": ""}
        self.trace.append(ev)
        text = (msg.get("content") or "").strip()
        # A saver stop message counts as operator-visible only when the main agent was stopped:
        # it is that run's final reply. A helper's stop message goes to the planner, not the user.
        if text and (not wn.get("stopped") or agent == "main"):
            self.said.append(text)
        self._note_nudges(ev)
        tail = ""
        if wn.get("done"):
            tail = f"saver: done ({_short(wn.get('reason', ''), 90)})"
        elif wn.get("stopped"):
            tail = f"saver: stop ({_short(wn.get('reason', ''), 90)})"
        ev["note"] = " then ".join(x for x in (ev["note"], tail) if x)
        return msg, wn

    def _note_nudges(self, ev: dict) -> None:
        """Say in the trace which detector made the saver nudge on this call."""
        flags: dict[str, int] = {}
        for p in self.proxies:
            for k, v in p.stats()["flags"].items():
                flags[k] = flags.get(k, 0) + v
        fired = [k for k, v in flags.items() if v > self.flags_seen.get(k, 0)]
        self.flags_seen = flags
        if fired:
            ev["note"] = "saver: nudge (" + ", ".join(fired) + ")"

    def run_tools(self, ev: dict, calls: list[dict], agent: str, exec_tool) -> list[dict]:
        out = []
        for c in calls:
            fn = c["function"]["name"]
            a = _args(c["function"].get("arguments", ""))
            res = exec_tool(fn, a)
            self.tool_calls += 1
            ev["calls"].append([fn, _short(a, 70), _short(res, 110)])
            out.append({"role": "tool", "tool_call_id": c["id"], "content": res})
        return out

    def world_tool(self, name: str, args: dict, counter: dict) -> str:
        """A world tool for one agent; with a quota, an agent may only make so many."""
        if self.quota is not None:
            counter["n"] = counter.get("n", 0) + 1
            if counter["n"] > self.quota:
                return json.dumps({"error": f"tool quota reached ({self.quota} per helper); "
                                            "delegate the rest to a new helper"})
        if name == "post_message":
            self.said.append(str(args.get("text", "")))
        return self.world.call(name, args)


def _assistant(msg: dict) -> dict:
    calls = msg.get("tool_calls") or []
    extra = {"tool_calls": calls} if calls else {}
    return {"role": "assistant", "content": msg.get("content"), **extra}


def react(run: Run, agent: str, system: str, prompt: str, tools: list[dict], cap: int,
          exec_tool) -> tuple[str, str]:
    """The ReAct loop. Returns (last reply text, how it ended)."""
    call = run.agent_call()
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    text = ""
    for _ in range(cap):
        msg, wn = run.model(call, agent, msgs, tools)
        text = (msg.get("content") or "").strip()
        if wn.get("done") or wn.get("stopped"):
            return text, "saver-done" if wn.get("done") else "saver-stop"
        calls = msg.get("tool_calls") or []
        msgs.append(_assistant(msg))
        if not calls:
            return text, "final"
        msgs.extend(run.run_tools(run.trace[-1], calls, agent, exec_tool))
    return text, "cap"


def stop_loop(run: Run, system: str, prompt: str, rule: str, cap: int,
              tools: list[dict]) -> str:
    """The stop-marker loop. Returns how it ended."""
    call = run.agent_call()
    counter: dict = {}
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    for _ in range(cap):
        msg, wn = run.model(call, "main", msgs, tools)
        if wn.get("done") or wn.get("stopped"):
            return "saver-done" if wn.get("done") else "saver-stop"
        calls = msg.get("tool_calls") or []
        msgs.append(_assistant(msg))
        if calls:
            msgs.extend(run.run_tools(run.trace[-1], calls, "main",
                                      lambda n, a: run.world_tool(n, a, counter)))
            continue
        if stop_rule_met((msg.get("content") or ""), rule):
            return "marker"
        msgs.append({"role": "user", "content": CONTINUE})
    return "cap"


def planner_loop(run: Run, system: str, prompt: str, cap: int, helper_cap: int,
                 depth: int = 1, base_tools: list[dict] | None = None) -> str:
    """A planner that delegates to helpers; each helper has its own cap and its own saver."""
    counters: dict[str, dict] = {"main": {}}

    def helper(task: str, level: int) -> str:
        if run.shared is not None:
            earlier = run.shared.find_redelegation(task)
            if earlier is not None:
                # the same subtask was already stopped for no progress: do not start a helper
                return f"{HELPER_STOPPED} It was already stopped earlier. {earlier}"
        run.helpers += 1
        name = f"helper{run.helpers}"
        counters[name] = {}
        tools = (base_tools or TOOLS) + ([DELEGATE] if level < depth else [])

        def exec_h(fn, a):
            if fn == "delegate":
                return helper(str(a.get("task", "")), level + 1)
            return run.world_tool(fn, a, counters[name])

        first_proxy = len(run.proxies)
        text, ended = react(run, name, SYS_HELPER, task, tools,
                            helper_cap, exec_h)
        mine = run.proxies[first_proxy:first_proxy + 1]
        no_progress = ended == "saver-stop" or (
            ended == "saver-done" and any(p.stats()["nudges"] for p in mine))
        if no_progress:
            # tell the planner plainly: this helper was stopped, do not hand the subtask on
            report = f"{HELPER_STOPPED} {text}".strip()
            if run.shared is not None:
                run.shared.note_stopped_task(task, report)
            return report
        if ended == "cap":
            run.helper_caps += 1
            return f"[helper hit its {helper_cap}-step limit] {text}"
        return text or "[helper returned nothing]"

    tools = (base_tools or TOOLS) + [DELEGATE]

    def exec_p(fn, a):
        if fn == "delegate":
            return helper(str(a.get("task", "")), 1)
        return run.world_tool(fn, a, counters["main"])

    _, ended = react(run, "main", system, prompt, tools, cap, exec_p)
    return ended


def run_case(case, client: Client, arm: str, *, effort: str = "low", rep: int = 0,
             purpose: str = "agentwaste-v2") -> HRun:
    """One (case, arm) run. `case` is a bench.cases.Case."""
    if arm not in ("harness", "harness+saver"):
        raise ValueError(f"unknown arm {arm}")
    saver = arm == "harness+saver"
    cap = {"react": REACT_CAP, "stop": STOP_CAP, "planner": PLANNER_CAP}[case.harness]
    world = case.world()
    run = Run(client, world, saver=saver, agent_cap=cap, effort=effort,
              case_id=case.id, quota=case.quota, shared=case.harness == "planner",
              purpose=purpose)
    res = HRun(case.harness, case.id, case.category, arm, rep)
    t0 = time.monotonic()
    tools = TOOLS + list(case.extra_tools)
    try:
        if case.harness == "react":
            counter: dict = {}
            _, res.ended_by = react(run, "main", case.system or SYS_REACT, case.prompt, tools,
                                    cap, lambda n, a: run.world_tool(n, a, counter))
        elif case.harness == "stop":
            res.ended_by = stop_loop(run, case.system or SYS_STOP, case.prompt, case.rule, cap,
                                     tools)
        else:
            res.ended_by = planner_loop(run, case.system or SYS_PLANNER, case.prompt, cap,
                                        HELPER_CAP, case.depth, tools)
    except SafetyStop:
        res.ended_by = "safety"
    except Exception as e:  # a failed call ends the run; it is reported, never hidden
        res.ended_by, res.error = "error", f"{type(e).__name__}: {str(e)[:160]}"
    res.wall_s = round(time.monotonic() - t0, 3)
    c = run.counting
    res.calls, res.prompt_tokens, res.cached_tokens = c.calls, c.prompt, c.cached
    res.completion_tokens, res.reasoning_tokens = c.completion, c.reasoning
    res.tool_calls = run.tool_calls
    res.mutations = sum(1 for m in world.mutations if m[0] in MUTATING)
    res.helpers, res.helper_caps = run.helpers, run.helper_caps
    res.nudges = sum(p.stats()["nudges"] for p in run.proxies)
    stops = [p.stats()["stop_reason"] for p in run.proxies if p.stats()["stop_reason"]]
    res.stop_reason = stops[-1] if stops else None
    for p in run.proxies:
        for k, v in p.stats()["flags"].items():
            res.flags[k] = res.flags.get(k, 0) + v
    if run.shared is not None:
        res.flags["redelegate"] = run.shared.redelegations
    res.miss = res.ended_by in ("cap", "safety") or res.helper_caps > 0
    res.said, res.trace = run.said, run.trace
    res.success = res.error is None and bool(case.check(world, "\n".join(run.said).lower()))
    return res
