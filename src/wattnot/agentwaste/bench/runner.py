"""Run the 24 tasks through an OpenAI-compatible client with the saver off and on.

Three arms, same tasks, same tools, same model settings:

  loop-off  An autonomous loop. The harness has no stop action: a plain-text reply is answered
            with "Continue." and the run ends only at the step cap.
  loop-on   The same loop behind `WasteProxy`: a `done` action, repeat / no-op / cycle checks,
            step and token budgets.
  auto-ref  A normal agent harness: the run ends when the model replies without a tool call.
            No saver. This is the reference for how much a well-behaved harness already saves.

`client(body, purpose=...)` is a `GuardedClient` in a real run and a fake in the tests. Tokens are
counted below the proxy, so they are what the upstream really billed.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

from wattnot.agentwaste.config import WasteConfig
from wattnot.agentwaste.proxy import WasteProxy

from .env import MUTATING, TOOLS, World
from .tasks import TASKS, Task

MODEL = "muse-spark-1.3"
ARMS = ("loop-off", "loop-on", "auto-ref")
MAX_STEPS = 12  # every real harness has a hard cap; the saver's own budget is the same number

SYS_LOOP = ("You are an autonomous operations agent. You work in a loop: each turn you may call "
            "tools. Use post_message to report results to the operator. Be concise.")
SYS_AUTO = ("You are an operations agent. Use the tools to finish the assignment, report with "
            "post_message, then reply with a short final answer. Be concise.")
CONTINUE = "Continue."

Client = Callable[..., dict]


@dataclass
class RunResult:
    task: str
    group: str
    arm: str
    success: bool
    rep: int = 0
    calls: int = 0              # model calls that reached the upstream
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    tool_calls: int = 0
    mutations: int = 0
    wall_s: float = 0.0
    ended_by: str = ""          # done | stopped | text | cap | error
    stop_reason: str | None = None
    nudges: int = 0
    error: str | None = None
    said: list[str] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def as_dict(self) -> dict:
        d = asdict(self)
        d["total_tokens"] = self.total_tokens
        return d


class _Counting:
    """Wraps the real client and tallies what the upstream billed."""

    def __init__(self, client: Client) -> None:
        self.client = client
        self.calls = self.prompt = self.cached = self.completion = self.reasoning = 0

    def __call__(self, body: Mapping, **kw) -> dict:
        r = self.client(body, **kw)
        u = r.get("usage") or {}
        self.calls += 1
        self.prompt += int(u.get("prompt_tokens", 0))
        self.completion += int(u.get("completion_tokens", 0))
        self.reasoning += int((u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0))
        self.cached += int((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
        return r


def _args(raw: str) -> dict:
    try:
        v = json.loads(raw or "{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def run_task(task: Task, client: Client, arm: str, *, effort: str = "low", max_tokens: int = 1500,
             waste: WasteConfig | None = None, max_steps: int = MAX_STEPS) -> RunResult:
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm}")
    world: World = task.world()
    counting = _Counting(client)
    proxy = WasteProxy(counting, waste or WasteConfig(max_steps=max_steps)) \
        if arm == "loop-on" else None
    call = proxy or counting
    msgs: list[dict] = [
        {"role": "system", "content": SYS_AUTO if arm == "auto-ref" else SYS_LOOP},
        {"role": "user", "content": task.prompt}]
    said: list[str] = []
    res = RunResult(task.id, task.group, arm, False)
    t0 = time.monotonic()
    try:
        for _ in range(max_steps):
            body = {"model": MODEL, "reasoning_effort": effort, "max_tokens": max_tokens,
                    "temperature": 0, "tools": TOOLS, "messages": msgs}
            r = call(body, purpose=f"agentwaste-bench:{arm}:{task.id}")
            msg = r["choices"][0]["message"]
            wn = r.get("wattnot") or {}
            text = (msg.get("content") or "").strip()
            if text:
                said.append(text)
            if wn.get("done") or wn.get("stopped"):
                res.ended_by = "done" if wn.get("done") else "stopped"
                break
            calls = msg.get("tool_calls") or []
            msgs.append({"role": "assistant", "content": msg.get("content"),
                         **({"tool_calls": calls} if calls else {})})
            if not calls:
                if arm == "auto-ref":
                    res.ended_by = "text"
                    break
                msgs.append({"role": "user", "content": CONTINUE})
                continue
            for c in calls:
                fn = c["function"]["name"]
                a = _args(c["function"].get("arguments", ""))
                out = world.call(fn, a)
                res.tool_calls += 1
                if fn == "post_message":
                    said.append(str(a.get("text", "")))
                msgs.append({"role": "tool", "tool_call_id": c["id"], "content": out})
        else:
            res.ended_by = "cap"
    except Exception as e:  # a failed call ends the run; it is reported, never hidden
        res.ended_by, res.error = "error", f"{type(e).__name__}: {str(e)[:160]}"
    res.wall_s = round(time.monotonic() - t0, 3)
    res.calls, res.prompt_tokens = counting.calls, counting.prompt
    res.cached_tokens = counting.cached
    res.completion_tokens, res.reasoning_tokens = counting.completion, counting.reasoning
    res.mutations = sum(1 for m in world.mutations if m[0] in MUTATING)
    if proxy is not None:
        st = proxy.stats()
        res.stop_reason, res.nudges = st.get("stop_reason"), st.get("nudges", 0)
    res.said = said
    res.success = res.error is None and bool(task.check(world, "\n".join(said).lower()))
    return res


def run_all(client: Client, *, arms=ARMS, tasks: list[Task] | None = None, reps: int = 1,
            workers: int = 4, seed: int = 7, **kw) -> list[RunResult]:
    """Every (task, arm, rep), shuffled so no arm gets a quieter API. Returned in a fixed order."""
    pool = tasks or TASKS
    jobs = [(t, a, r) for r in range(reps) for t in pool for a in arms]
    shuffled = list(jobs)
    random.Random(seed).shuffle(shuffled)

    def one(j):
        res = run_task(j[0], client, j[1], **kw)
        res.rep = j[2]
        return res

    with ThreadPoolExecutor(workers) as ex:
        out = list(ex.map(one, shuffled))
    order = {(t.id, a, r): i for i, (t, a, r) in enumerate(jobs)}
    return sorted(out, key=lambda x: order[(x.task, x.arm, x.rep)])
