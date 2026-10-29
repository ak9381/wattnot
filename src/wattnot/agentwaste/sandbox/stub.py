"""Turn a recorded trace's tool calls into a typed, deterministic API stub.

Code Mode hands the agent its tools as an *API* and asks for one program instead of many
round trips. To run that program offline, honestly, with no live model and no network, we
synthesise the tool API from what the recorded trace already saw:

* the distinct tool names become methods on a ``Tools`` class (a *typed* stub -- each method's
  signature is inferred from the argument shapes the trace recorded, so calling it wrongly is
  a ``TypeError``, same as a real typed client);
* every ``(tool, arguments)`` pair the trace recorded becomes a fixed answer, so a call
  **replays the exact recorded output** and the same inputs always return the same bytes
  (determinism is a tested property, not a hope).

Nothing here estimates anything: the stub is a faithful replay of the trace. The generated
Python source is what the sandbox imports; it reads its recordings from a sidecar JSON file so
the source stays small and the data stays inspectable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


def _canon_args(args: str | dict) -> str:
    """A stable key for one argument set: parse JSON when we can, else the raw string.

    Sorted keys make ``{"a":1,"b":2}`` and ``{"b":2,"a":1}`` the same recorded call, so replay
    does not depend on the upstream's key order.
    """
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (ValueError, TypeError):
            return args.strip()
    try:
        return json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        return str(args)


@dataclass(frozen=True)
class ToolSpec:
    """One tool inferred from the trace: its name and the argument keys ever seen for it."""

    name: str
    params: tuple[str, ...]


def collect_calls(trace: list[dict]) -> list[tuple[str, str, str]]:
    """Flatten a trace's per-step ``calls`` into ``(tool, canonical_args, output)`` triples.

    Order is preserved (the order the agent actually called the tools). Outputs are the exact
    recorded strings; we never touch them.
    """
    out: list[tuple[str, str, str]] = []
    for step in trace:
        for call in step.get("calls") or []:
            if not call:
                continue
            name = call[0]
            raw_args = call[1] if len(call) > 1 else "{}"
            output = call[2] if len(call) > 2 else ""
            out.append((name, _canon_args(raw_args), output if isinstance(output, str)
                        else json.dumps(output, ensure_ascii=False)))
    return out


def infer_specs(calls: list[tuple[str, str, str]]) -> list[ToolSpec]:
    """Infer one :class:`ToolSpec` per distinct tool from the argument keys the trace used."""
    params: dict[str, list[str]] = {}
    for name, cargs, _ in calls:
        keys: list[str] = []
        try:
            parsed = json.loads(cargs)
            if isinstance(parsed, dict):
                keys = list(parsed)
        except (ValueError, TypeError):
            keys = []
        bucket = params.setdefault(name, [])
        for k in keys:
            if k not in bucket:
                bucket.append(k)
    return [ToolSpec(name=n, params=tuple(params[n])) for n in sorted(params)]


def build_recordings(calls: list[tuple[str, str, str]]) -> dict:
    """The replay table the stub reads: per tool, canonical-args -> recorded output.

    A ``default`` per tool (the first output it ever returned) keeps an unseen-but-plausible
    call deterministic instead of raising, which is what a one-shot program needs when it
    composes a query the multi-turn trace happened not to make verbatim.
    """
    table: dict[str, dict] = {}
    for name, cargs, output in calls:
        slot = table.setdefault(name, {"by_args": {}, "default": output})
        slot["by_args"].setdefault(cargs, output)
    return table


_METHOD = '''
    def {name}(self{params}) -> str:
        """Replayed tool `{name}` (recorded output; deterministic)."""
        return self._lookup({name!r}, {argexpr})
'''


def render_stub_source(specs: list[ToolSpec]) -> str:
    """Generate the Python source of the typed ``Tools`` stub.

    Each method takes exactly the keyword arguments the trace recorded for that tool (so a
    wrong call is a ``TypeError``), canonicalises them the same way the recordings were keyed,
    and returns the recorded string. The class loads its replay table from ``recordings.json``
    beside it -- no network, no state, pure function of its inputs.
    """
    header = '''"""Generated typed tool API stub (replay of a recorded trace). Do not edit by hand."""
import json
from pathlib import Path


def _canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Tools:
    def __init__(self, recordings_path="recordings.json"):
        self._table = json.loads(Path(recordings_path).read_text())
        self.calls = 0

    def _lookup(self, name, args):
        self.calls += 1
        slot = self._table.get(name) or {}
        return slot.get("by_args", {}).get(_canon(args), slot.get("default", ""))
'''
    methods = []
    for spec in specs:
        if spec.params:
            params = "".join(f", {p}" for p in spec.params)
            argexpr = "{" + ", ".join(f"{p!r}: {p}" for p in spec.params) + "}"
        else:
            params = ""
            argexpr = "{}"
        methods.append(_METHOD.format(name=spec.name, params=params, argexpr=argexpr))
    return header + "".join(methods)


def generate_program(specs: list[ToolSpec], calls: list[tuple[str, str, str]], task: str) -> str:
    """Synthesise the one-turn Code Mode program from the trace's distinct calls.

    The multi-turn trace repeated and re-queried; the Code Mode rewrite issues each *distinct*
    ``(tool, args)`` once, in order, collects the outputs, and composes a single answer. This
    is a faithful straight-line equivalent of the loop a model would emit -- generated
    deterministically from the trace, not by any live model call.
    """
    seen: set[tuple[str, str]] = set()
    lines = [
        "# Code Mode program (generated from the recorded trace, one turn).",
        "# `tools` is injected by the sandbox bootstrap; see stub.py.",
        "import json",
        "outputs = []",
    ]
    for name, cargs, _ in calls:
        key = (name, cargs)
        if key in seen:
            continue
        seen.add(key)
        try:
            parsed = json.loads(cargs)
        except (ValueError, TypeError):
            parsed = {}
        if isinstance(parsed, dict) and parsed:
            kw = ", ".join(f"{k}={v!r}" for k, v in parsed.items())
        else:
            kw = ""
        lines.append(f"outputs.append(tools.{name}({kw}))")
    lines += [
        "# Compose the final result from the collected tool outputs.",
        "answer = {",
        f"    'task': {task!r},",
        "    'tool_calls': len(outputs),",
        "    'distinct_calls': len(outputs),",
        "    'result': outputs[-1] if outputs else '',",
        "    'collected': outputs,",
        "}",
    ]
    return "\n".join(lines) + "\n"
