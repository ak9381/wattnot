"""Reasoning effort: what to send for each level, and which order to try them in.

Muse Spark Open always reasons, and the default level is `xhigh`. Reasoning
tokens are billed as output, so effort is the biggest cost knob we have.

Two things scale with effort:

* `max_tokens` caps the whole completion, reasoning included. Too low and the
  model runs out while thinking and returns no answer. Too high and a runaway
  answer is expensive. So the cap is the answer budget plus an allowance for
  reasoning that grows with the level.
* stop sequences: lower levels get tighter tail trimming so a short answer does
  not turn into a ramble. The quality gate sees the effect.

The agent (Muse Spark) only proposes the order in which to try the levels. The
gates decide what is kept. A bad proposal costs a few extra calls, never quality.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

from wattnot.cloud import EFFORTS, MODEL, REFERENCE

# Tokens the model may spend thinking, on top of the answer budget. Starting values;
# the first live run replaces them with measured numbers (see `calibrated_allowance`).
DEFAULT_ALLOWANCE = {"low": 1500, "medium": 4000, "high": 12000, "xhigh": 32000}
MAX_TOKENS_CEILING = 65536  # higher max_tokens values are scheduled slightly later

TAIL_STOPS = {"low": ["\n\n\n", "\n---"], "medium": ["\n\n\n"], "high": [], "xhigh": []}
CANDIDATES = tuple(e for e in EFFORTS if e != REFERENCE)  # the levels we try to drop to


def max_tokens_for(effort: str, answer_budget: int,
                   allowance: Mapping[str, int] = DEFAULT_ALLOWANCE) -> int:
    if effort not in EFFORTS:
        raise ValueError(f"unknown effort {effort!r}")
    return min(MAX_TOKENS_CEILING, int(answer_budget) + int(allowance[effort]))


def stop_for(effort: str, item_stop: Sequence[str] = ()) -> list[str]:
    if effort not in EFFORTS:
        raise ValueError(f"unknown effort {effort!r}")
    seen: list[str] = []
    for s in [*item_stop, *TAIL_STOPS[effort]]:
        if s and s not in seen:
            seen.append(s)
    return seen[:4]  # most OpenAI-style APIs allow four


def calibrated_allowance(observed_completion_tokens: Mapping[str, Sequence[int]],
                         answer_budget: int, *, headroom: float = 1.3,
                         floor: Mapping[str, int] = DEFAULT_ALLOWANCE) -> dict[str, int]:
    """After a probe run: allowance = p95 of what each level really used, plus headroom."""

    out = dict(floor)
    for effort, toks in observed_completion_tokens.items():
        if toks:
            s = sorted(toks)
            p95 = s[min(len(s) - 1, int(0.95 * len(s)))]
            out[effort] = max(256, int(p95 * headroom) - answer_budget)
    return out


# ---- the proposal ---------------------------------------------------------------------------

PROPOSE_SYSTEM = (
    "You plan an experiment for a hosted reasoning model. For each prompt class, say in which "
    "order to try the reasoning effort levels low, medium and high, cheapest-likely-to-pass first. "
    "A level that already passes means the more expensive ones are skipped. "
    "Reply with JSON only: {\"orders\": {\"<class>\": [\"low\", \"medium\", \"high\"]}, "
    "\"note\": \"<one line>\"}."
)


def default_order() -> list[str]:
    return list(CANDIDATES)


def summarize_classes(classes: Mapping[str, Sequence]) -> dict:
    """The only thing sent to the planner: class names and sizes. No prompt text."""

    return {"efforts": list(CANDIDATES), "classes": {
        name: {"items": len(items),
               "avg_prompt_chars": round(sum(len(i.prompt) for i in items) / len(items)),
               "avg_answer_budget_tokens": round(sum(i.max_tokens for i in items) / len(items))}
        for name, items in classes.items()}}


def proposal_body(summary: Mapping) -> dict:
    return {"model": MODEL, "reasoning_effort": "low", "max_tokens": 1500, "temperature": 0,
            "messages": [{"role": "system", "content": PROPOSE_SYSTEM},
                         {"role": "user", "content": json.dumps(summary, separators=(",", ":"))}]}


def parse_orders(content: str, class_names: Sequence[str]) -> dict[str, list[str]]:
    """Validate the planner's JSON. Unknown levels are dropped, missing ones appended."""

    m = re.search(r"\{.*\}", content, flags=re.DOTALL)
    if not m:
        raise ValueError("no JSON object in the proposal")
    raw = json.loads(m.group(0)).get("orders")
    if not isinstance(raw, dict):
        raise ValueError("proposal has no 'orders' object")
    out: dict[str, list[str]] = {}
    for name in class_names:
        got = raw.get(name)
        order = [e for e in (got if isinstance(got, list) else []) if e in CANDIDATES]
        order = list(dict.fromkeys(order))
        out[name] = order + [e for e in CANDIDATES if e not in order]
    return out
