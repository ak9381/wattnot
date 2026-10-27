from __future__ import annotations

import hashlib
import json
import re
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
    """A fired detector. kind is one of repeat, no_op, cycle, reasoning, error_storm, low_gain,
    paraphrase."""

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


# ---- second-layer detectors: the failures a harness stop rule tends to miss -----------------

_WORD = re.compile(r"[a-z]{3,}")  # words only: ids, counters and timestamps are not new information
_META_KEYS = ("results", "items", "matches", "hits", "rows")


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def classify_result(result: str | None) -> str:
    """'error', 'empty' or 'ok'. Only a clear error object or an empty answer is not 'ok'."""
    if result is None:
        return "ok"
    text = result.strip()
    if not text or text in ("[]", "{}", "null"):
        return "empty"
    try:
        obj = json.loads(text)
    except ValueError:
        return "error" if re.match(r"(?i)\s*(error|http 5\d\d|5\d\d )", text) else "ok"
    if isinstance(obj, list):
        return "empty" if not obj else "ok"
    if isinstance(obj, dict):
        status = obj.get("status")
        if "error" in obj or (isinstance(status, int) and status >= 500):
            return "error"
        for key in _META_KEYS:
            if key in obj and isinstance(obj[key], list) and not obj[key]:
                return "empty"
    return "ok"


def find_error_storm(steps: list[Step], error_limit: int, empty_limit: int) -> Flag | None:
    """Flag one tool that failed (or came back empty) again and again, whatever the arguments.

    A retry storm changes the arguments or carries a fresh request id each time, so the
    byte-for-byte repeat and no-op checks never see it. The tool must also be in the last step:
    an old storm the agent has since left is not fresh waste.
    """
    if not steps or not steps[-1].calls:
        return None
    by_tool: dict[str, list[str]] = {}
    for step in steps:
        for call in step.calls:
            if call.result is not None:
                by_tool.setdefault(call.name, []).append(classify_result(call.result))
    for call in steps[-1].calls:
        kinds = by_tool.get(call.name, [])
        if not kinds or kinds[-1] == "ok":
            continue
        run = 0
        for k in reversed(kinds):
            if k != kinds[-1]:
                break
            run += 1
        limit = error_limit if kinds[-1] == "error" else empty_limit
        if limit and run >= limit:
            what = "failed" if kinds[-1] == "error" else "came back empty"
            return Flag("error_storm", f"tool {call.name} {what} {run} times in a row")
    return None


def _progress(call: Call) -> bool:
    """A call that changed something has made progress, whatever words its result repeats."""
    return '"changed":true' in (call.result or "").replace(" ", "")


def find_low_gain(steps: list[Step], window: int, max_new: float) -> Flag | None:
    """Flag the last `window` calls when each added almost no new words to what the run knew.

    Reads and searches that return the same facts again (a different ordering, a new score or
    timestamp) add nothing. A call that changed something counts as progress. Each call is
    compared with everything its run had read before it.
    """
    if window <= 0:
        return None
    seen: set[str] = set()
    gains: list[float] = []
    for step in steps:
        for call in step.calls:
            if call.result is None:
                continue
            words = _words(call.result)
            if _progress(call):
                gains.append(1.0)
            elif not words:
                gains.append(0.0)
            else:
                gains.append(len(words - seen) / len(words))
            seen |= words
    if len(gains) > window and all(g <= max_new for g in gains[-window:]):
        return Flag("low_gain", f"the last {window} calls added almost no new information")
    return None


def _overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def find_paraphrase(messages: list, limit: int, sim: float = 0.6) -> Flag | None:
    """Flag a run that keeps answering with text and never finishes.

    Looks at the assistant turns at the tail that carry no tool call. Fires when the last
    `limit` of them say nearly the same thing, or when three come in a row.
    """
    if limit <= 0:
        return None
    tail: list[set[str]] = []
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "assistant":
            if msg.get("tool_calls"):
                break
            text = msg.get("content")
            tail.append(_words(text if isinstance(text, str) else ""))
        elif msg.get("role") == "tool":
            break
    tail.reverse()
    if len(tail) >= 3:
        return Flag("paraphrase", f"{len(tail)} text replies in a row and no finish")
    if len(tail) >= limit and all(len(w) >= 3 for w in tail[-limit:]):
        last = tail[-limit:]
        if all(_overlap(last[i], last[i + 1]) >= sim for i in range(len(last) - 1)):
            return Flag("paraphrase", "the last replies say the same thing again")
    return None


def same_task(a: str, b: str, sim: float = 0.6) -> bool:
    """True when two sub-task descriptions ask for nearly the same thing (word overlap)."""
    return len(_words(a)) >= 3 and len(_words(b)) >= 3 and _overlap(_words(a), _words(b)) >= sim
