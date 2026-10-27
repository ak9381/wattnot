from __future__ import annotations

import copy
import json
import threading
import time
from dataclasses import dataclass, field

from wattnot.agentwaste.config import WasteConfig
from wattnot.agentwaste.detect import (
    Call,
    Flag,
    Step,
    canon_args,
    find_cycle,
    find_error_storm,
    find_low_gain,
    find_no_op,
    find_paraphrase,
    find_reasoning_overspend,
    find_repeat,
    same_task,
)

_EFFORT_DOWN = {"high": "medium", "medium": "low", "low": "minimal"}

_STOP_REASONS = {
    "repeat": "repeated calls",
    "no_op": "no new information",
    "cycle": "plan cycle",
    "error_storm": "a tool keeps failing",
    "low_gain": "no new information",
    "paraphrase": "same answer again and again",
}
_NEW_KINDS = {"error_storm", "low_gain", "paraphrase"}
# Stops that mean "the agent is going round in circles" (vs. a budget running out).
_NO_PROGRESS = set(_STOP_REASONS.values())
_OBS_CHARS = 220

# What the planner reads when a helper was stopped, and when it asks for that subtask again.
HELPER_STOPPED = "stopped: no progress, do not re-delegate this subtask."


def last_observation(messages: list) -> tuple[str, str] | None:
    """The latest tool result in a chat, as (tool name, text), trimmed. None if there is none."""
    names: dict[str, str] = {}
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            for call in msg.get("tool_calls") or []:
                if isinstance(call, dict) and call.get("id") is not None:
                    names[str(call["id"])] = str((call.get("function") or {}).get("name", ""))
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "tool":
            text = " ".join(_message_text(msg.get("content")).split())
            if len(text) > _OBS_CHARS:
                text = text[: _OBS_CHARS - 1] + "…"
            return names.get(str(msg.get("tool_call_id")), "tool"), text
    return None


class SharedBudget:
    """One budget for a whole run: the main agent and every helper it starts.

    A harness usually caps each agent's own loop. Helpers have their own counters, so the run as
    a whole is not capped at all. Guards that share one SharedBudget count against the same
    calls, tokens and number of helpers.
    """

    def __init__(self, max_calls: int = 12, max_tokens: int = 60000, max_agents: int = 4) -> None:
        self.max_calls, self.max_tokens, self.max_agents = max_calls, max_tokens, max_agents
        self.calls = self.tokens = self.agents = 0
        self._lock = threading.Lock()
        self._stopped_tasks: list[tuple[str, str]] = []
        self.redelegations = 0

    def note_stopped_task(self, task: str, report: str) -> None:
        """A helper was stopped on this subtask. Remember it for the whole run."""
        with self._lock:
            self._stopped_tasks.append((task, report))

    def find_redelegation(self, task: str) -> str | None:
        """The report of an earlier stopped helper whose subtask is nearly this one, or None."""
        with self._lock:
            for old, report in self._stopped_tasks:
                if same_task(old, task):
                    self.redelegations += 1
                    return report
        return None

    def register_agent(self) -> bool:
        """Count one more agent. False when the run already has max_agents."""
        with self._lock:
            self.agents += 1
            return self.agents <= self.max_agents

    def charge(self, tokens: int) -> None:
        with self._lock:
            self.calls += 1
            self.tokens += int(tokens)

    def reason(self) -> str | None:
        with self._lock:
            if self.calls >= self.max_calls:
                return "shared call budget reached"
            if self.tokens >= self.max_tokens:
                return "shared token budget reached"
        return None


@dataclass
class Verdict:
    """Guard decision. body is the (possibly adjusted) request to send."""

    action: str  # "go" | "stop"
    body: dict | None
    reason: str | None = None
    flags: list[Flag] = field(default_factory=list)


def _message_text(content: object) -> str:
    """Best-effort string form of a message content value."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, sort_keys=True, separators=(",", ":"),
                          default=str)
    except (TypeError, ValueError):
        return str(content)


def build_trace(messages: list) -> list[Step]:
    """Build steps from chat messages. Each assistant message with tool_calls
    is one step; each tool call id matches the following tool message. A later
    text-only assistant turn in a run that already used tools is an idle step."""
    results: dict[str, str] = {}
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "tool":
            call_id = msg.get("tool_call_id")
            if call_id is not None:
                results[str(call_id)] = _message_text(msg.get("content"))
    steps: list[Step] = []
    for pos, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            # A text-only turn that was followed by another request: the loop went on
            # without the agent doing anything. It only counts once the run has used tools.
            if any(s.calls for s in steps) and pos < len(messages) - 1:
                steps.append(Step(idle=True))
            continue
        calls = []
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            func = call.get("function") or {}
            call_id = call.get("id")
            result = results.get(str(call_id)) if call_id is not None else None
            calls.append(Call(
                name=str(func.get("name", "")),
                args=canon_args(func.get("arguments") or ""),
                result=result,
            ))
        steps.append(Step(calls=calls))
    return steps


def done_tool_schema(name: str = "done") -> dict:
    """Tool schema that lets the agent stop cleanly with a one-line reason."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": (
                "Call this when the goal is complete or there is nothing "
                "left to do. Give a one-line reason."
            ),
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    }


def _tool_names(tools: list) -> set[str]:
    names = set()
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        func = tool.get("function")
        if isinstance(func, dict) and func.get("name"):
            names.add(str(func["name"]))
        elif tool.get("name"):
            names.add(str(tool["name"]))
    return names


class WasteGuard:
    """Per-run waste state. One instance tracks one agent run."""

    def __init__(self, config: WasteConfig | None = None,
                 shared: SharedBudget | None = None) -> None:
        self._config = config or WasteConfig()
        self._shared = shared
        self._too_many = shared is not None and not shared.register_agent()
        self._upstream_calls = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._reasoning_tokens = 0
        self._reasoning_hist: list[int] = []
        self._reasoning_pending: Flag | None = None
        self._nudges = 0
        self._flag_counts = {"repeat": 0, "no_op": 0, "cycle": 0, "reasoning": 0,
                             "error_storm": 0, "low_gain": 0, "paraphrase": 0}
        self._stopped = False
        self._stop_reason: str | None = None
        self._done_called = False
        self._done_reason: str | None = None
        self._last_obs: tuple[str, str] | None = None

    def _stop(self, reason: str) -> None:
        self._stopped = True
        self._stop_reason = reason

    def before(self, body: dict) -> Verdict:
        """Check budgets and detectors. Never mutates the caller's body."""
        cfg = self._config
        self._last_obs = last_observation(body.get("messages", []) or [])
        if self._shared is not None:
            why = "too many helper agents" if self._too_many else self._shared.reason()
            if why:
                self._stop(why)
                return Verdict("stop", None, why, [])
        if self._upstream_calls >= cfg.max_steps:
            self._stop("step budget reached")
            return Verdict("stop", None, "step budget reached", [])
        if self._prompt_tokens + self._completion_tokens >= cfg.max_total_tokens:
            self._stop("token budget reached")
            return Verdict("stop", None, "token budget reached", [])

        trace = build_trace(body.get("messages", []) or [])
        fired: Flag | None = None
        for finder in (
            lambda: find_repeat(trace, cfg.repeat_limit),
            lambda: find_no_op(trace, cfg.noop_limit),
            lambda: find_cycle(trace, cfg.cycle_max_len),
            lambda: find_error_storm(trace, cfg.error_limit, cfg.empty_limit),
            lambda: find_low_gain(trace, cfg.low_gain_window, cfg.low_gain_max_new),
            lambda: find_paraphrase(body.get("messages", []) or [], cfg.paraphrase_limit),
        ):
            fired = finder()
            if fired is not None:
                break

        flags: list[Flag] = []
        out: dict | None = None
        if fired is not None:
            reason = _STOP_REASONS[fired.kind]
            self._flag_counts[fired.kind] += 1
            if self._nudges >= cfg.max_nudges:
                self._stop(reason)
                return Verdict("stop", None, reason, [fired])
            out = copy.deepcopy(body)
            if fired.kind in _NEW_KINDS:
                note = (f"Note: {fired.detail}. Do not repeat it. If the work is finished, or "
                        f"you are blocked, call the `{cfg.done_tool}` tool with a one-line "
                        "reason (say what blocked you) instead of making more calls.")
            else:
                note = (f"Note: {fired.detail}. If the goal is done or there is "
                        f"nothing left to do, call the `{cfg.done_tool}` tool "
                        "with a one-line reason instead of making more calls.")
            out.setdefault("messages", []).append({"role": "user", "content": note})
            self._nudges += 1
            flags.append(fired)

        if self._reasoning_pending is not None:
            if out is None:
                out = copy.deepcopy(body)
            effort = out.get("reasoning_effort")
            if effort in _EFFORT_DOWN:
                out["reasoning_effort"] = _EFFORT_DOWN[effort]
            existing = out.get("max_tokens")
            if isinstance(existing, int) and not isinstance(existing, bool):
                out["max_tokens"] = min(existing, 2000)
            else:
                out["max_tokens"] = min(4000, 2000)
            flags.append(self._reasoning_pending)
            self._flag_counts["reasoning"] += 1
            self._reasoning_pending = None

        tools = (out if out is not None else body).get("tools")
        if (
            cfg.inject_done
            and isinstance(tools, list)
            and tools
            and cfg.done_tool not in _tool_names(tools)
        ):
            if out is None:
                out = copy.deepcopy(body)
            out["tools"] = [*out["tools"], done_tool_schema(cfg.done_tool)]

        return Verdict("go", out if out is not None else body, None, flags)

    def after(self, response: dict) -> dict:
        """Record usage for one upstream call. Maps a done call to a stop."""
        cfg = self._config
        usage = response.get("usage") or {}
        prompt = usage.get("prompt_tokens") or 0
        completion = usage.get("completion_tokens") or 0
        details = usage.get("completion_tokens_details") or {}
        reasoning = details.get("reasoning_tokens") or 0
        self._prompt_tokens += int(prompt)
        self._completion_tokens += int(completion)
        self._reasoning_tokens += int(reasoning)
        self._upstream_calls += 1
        if self._shared is not None:
            self._shared.charge(int(prompt) + int(completion))
        self._reasoning_hist.append(int(reasoning))
        hist = [Step(reasoning_tokens=v) for v in self._reasoning_hist]
        self._reasoning_pending = find_reasoning_overspend(
            hist, cfg.reasoning_floor, cfg.reasoning_ratio, cfg.reasoning_hard_cap
        )

        choices = response.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        for call in message.get("tool_calls") or []:
            func = (call or {}).get("function") or {}
            if func.get("name") != cfg.done_tool:
                continue
            raw_args = func.get("arguments") or ""
            try:
                parsed = json.loads(raw_args) if isinstance(raw_args, str) else {}
                reason = str(parsed.get("reason", raw_args))
            except ValueError:
                reason = str(raw_args)
            self._done_called = True
            self._done_reason = reason
            self._stop(f"done: {reason}")
            out = copy.deepcopy(response)
            first = out["choices"][0]
            first["message"]["content"] = reason
            first["message"].pop("tool_calls", None)
            first["finish_reason"] = "stop"
            out["wattnot"] = {"done": True, "reason": reason}
            return out
        return response

    def stop_response(self, reason: str, model: str = "") -> dict:
        """A valid chat completion that reports an early stop."""
        self._stop(reason)
        content = f"Stopped early: {reason}."
        if reason in _NO_PROGRESS:
            content += " Nothing new came back, so the task was not finished."
        else:
            content += " The task was not finished."
        wn: dict = {"stopped": True, "reason": reason}
        if self._last_obs is not None:
            tool, text = self._last_obs
            content += f" Latest result (from {tool}): {text}"
            wn["last_result"] = {"tool": tool, "text": text}
        return {
            "id": "wattnot-stop",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
            "wattnot": wn,
        }

    def stats(self) -> dict:
        """Current counters for this run."""
        return {
            "upstream_calls": self._upstream_calls,
            "prompt_tokens": self._prompt_tokens,
            "completion_tokens": self._completion_tokens,
            "reasoning_tokens": self._reasoning_tokens,
            "nudges": self._nudges,
            "flags": dict(self._flag_counts),
            "stopped": self._stopped,
            "stop_reason": self._stop_reason,
            "done_called": self._done_called,
        }
