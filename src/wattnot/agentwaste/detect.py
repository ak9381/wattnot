from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass, field


@dataclass
class Call:
    """One tool call. args is canonical JSON: sorted keys, no spaces."""

    name: str
    args: str
    result: str | None = None


@dataclass
class Step:
    """One assistant turn: the tool calls it made plus token counts."""

    calls: list[Call] = field(default_factory=list)
    reasoning_tokens: int = 0
    completion_tokens: int = 0
    idle: bool = False  # a text-only turn that the harness answered with another request


@dataclass
class Flag:
    """A fired detector. kind is one of repeat, no_op, cycle, reasoning."""

    kind: str
    detail: str


def canon_args(raw: str | dict) -> str:
    """Return canonical JSON for args: sorted keys, no spaces."""
    if isinstance(raw, dict):
        return json.dumps(raw, sort_keys=True, separators=(",", ":"))
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
        except ValueError:
            return raw
        return json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return json.dumps(raw, sort_keys=True, separators=(",", ":"))


def find_repeat(steps: list[Step], limit: int) -> Flag | None:
    """Flag a (name, args, result-hash) triple seen limit or more times.

    Only a triple that the last step repeated counts: an old repeat the agent has since moved
    on from is not a fresh sign of waste.
    """
    counts: dict[tuple[str, str, str], int] = {}
    first: dict[tuple[str, str, str], tuple[str, str]] = {}
    for step in steps:
        for call in step.calls:
            if call.result is None:
                continue
            digest = hashlib.sha256(call.result.encode()).hexdigest()
            key = (call.name, call.args, digest)
            counts[key] = counts.get(key, 0) + 1
            if key not in first:
                first[key] = (call.name, call.args)
    latest = {
        (c.name, c.args, hashlib.sha256(c.result.encode()).hexdigest())
        for c in (steps[-1].calls if steps else []) if c.result is not None
    }
    for key, n in counts.items():
        if n >= limit and key in latest:
            name, args = first[key]
            return Flag("repeat", f"call {name} {args} repeated {n} times")
    return None


def _reports_no_change(result: str | None) -> bool:
    """True when the result itself reports that nothing changed."""
    if result is None:
        return False
    text = result.strip()
    if text.lower().startswith("no change"):
        return True
    try:
        obj = json.loads(text)
    except ValueError:
        return False
    return isinstance(obj, dict) and obj.get("changed") is False


def find_no_op(steps: list[Step], limit: int) -> Flag | None:
    """Flag when the last limit steps are all no-op steps."""
    if len(steps) < limit:
        return None
    seen: set[str] = set()
    noop: list[bool] = []
    for step in steps:
        if not step.calls:
            noop.append(step.idle)
            continue
        is_noop = all(
            call.result is not None
            and (call.result in seen or _reports_no_change(call.result))
            for call in step.calls
        )
        noop.append(is_noop)
        for call in step.calls:
            if call.result is not None:
                seen.add(call.result)
    if all(noop[-limit:]):
        return Flag("no_op", f"last {limit} steps added no new information")
    return None


def find_cycle(steps: list[Step], max_len: int) -> Flag | None:
    """Flag a repeated block of 2..max_len distinct calls at the tail."""
    sigs = [(call.name, call.args) for step in steps for call in step.calls]
    n = len(sigs)
    for size in range(2, min(max_len, n // 2) + 1):
        block = sigs[-size:]
        if block == sigs[-2 * size : -size] and len(set(block)) >= 2:
            names = " -> ".join(name for name, _ in block)
            return Flag("cycle", f"plan cycle repeating: {names}")
    return None


def find_reasoning_overspend(
    steps: list[Step], floor: int, ratio: float, hard_cap: int
) -> Flag | None:
    """Flag when the last step spent far more reasoning tokens than before."""
    if not steps:
        return None
    last = steps[-1].reasoning_tokens
    if last > hard_cap:
        return Flag("reasoning", f"reasoning tokens {last} above hard cap {hard_cap}")
    if len(steps) >= 3:
        median = statistics.median(s.reasoning_tokens for s in steps[:-1])
        threshold = max(floor, ratio * median)
        if last > threshold:
            return Flag(
                "reasoning",
                f"reasoning tokens {last} above threshold {threshold:g}",
            )
    return None
