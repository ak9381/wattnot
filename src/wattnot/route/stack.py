"""Stack mode: WattNot behind a token trimmer, and the savings add up.

A token trimmer (a prompt-compression proxy, an agent-cost harness, or the small built-in one
here) cuts the tokens a request sends. WattNot's caps cut the joules each remaining token
costs. They act on different factors, so the energy saving composes:

    energy_after / energy_before = (tokens_after / tokens_before) * (J_per_token_after / J_per_token_before)

The receipt row shows each layer and the stacked total, with the weakest label of the inputs.
`StackedTransport` wraps any OpenAI-style transport: it trims the body, sends it, and counts
tokens from the reply's `usage` (MEASURED) or from characters / 4 when usage is missing
(ESTIMATE).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping

from wattnot.route.escalate import weakest

Transport = Callable[[dict], dict]
TOOL_MAX_CHARS = 4000


def trim_body(body: Mapping) -> dict:
    """The built-in trimmer: drop exact repeat messages, collapse runs of blank lines and cap
    very long tool outputs. Never touches the last user message."""

    msgs = list(body.get("messages") or [])
    out, seen = [], set()
    last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=-1)
    for i, m in enumerate(msgs):
        c = m.get("content")
        if isinstance(c, str) and i != last_user:
            key = (m.get("role"), c)
            if key in seen:
                continue
            seen.add(key)
            c = "\n".join(x for j, x in enumerate(c.split("\n"))
                          if x.strip() or (j and c.split("\n")[j - 1].strip()))
            if m.get("role") == "tool" and len(c) > TOOL_MAX_CHARS:
                c = c[:TOOL_MAX_CHARS] + f"\n[trimmed {len(c) - TOOL_MAX_CHARS} chars]"
            m = {**m, "content": c}
        out.append(m)
    return {**body, "messages": out}


def _chars(body: Mapping) -> int:
    return len(json.dumps(body.get("messages") or [], ensure_ascii=False))


class StackedTransport:
    """trimmer -> inner transport. Keeps running token totals for the receipt row."""

    def __init__(self, inner: Transport, trimmer: Callable[[Mapping], dict] = trim_body) -> None:
        self.inner, self.trimmer = inner, trimmer
        self.tokens_before = self.tokens_after = 0
        self.label = "MEASURED"

    def __call__(self, body: dict) -> dict:
        trimmed = self.trimmer(body)
        before_c, after_c = _chars(body), _chars(trimmed)
        reply = self.inner(trimmed)
        used = (reply.get("usage") or {}).get("prompt_tokens")
        if used:
            # the reply measures the trimmed prompt; the untrimmed one scales by characters
            self.tokens_after += int(used)
            self.tokens_before += round(int(used) * before_c / max(after_c, 1))
            self.label = weakest([self.label, "ESTIMATE"]) if before_c != after_c else self.label
        else:
            self.tokens_after += after_c // 4
            self.tokens_before += before_c // 4
            self.label = "ESTIMATE"
        return reply


def stack_row(*, tokens_before: int, tokens_after: int, tokens_label: str,
              j_per_token_change_pct: float, caps_label: str,
              usd_per_mtok: float | None = None, trimmer: str = "built-in",
              same_workload: bool = True) -> dict:
    """One receipt row: tokens saved, watts saved, and the two stacked."""

    if tokens_before <= 0:
        raise ValueError("tokens_before must be positive")
    t = tokens_after / tokens_before
    c = 1 + j_per_token_change_pct / 100
    row = {"trimmer": trimmer,
           "tokens": {"before": tokens_before, "after": tokens_after,
                      "saved_pct": round(100 * (1 - t), 2), "label": tokens_label},
           "caps": {"j_per_token_change_pct": round(j_per_token_change_pct, 2), "label": caps_label},
           "energy_saved_pct": {"tokens_only": round(100 * (1 - t), 2),
                                "caps_only": round(100 * (1 - c), 2),
                                "stacked": round(100 * (1 - t * c), 2),
                                "label": weakest([tokens_label, caps_label])
                                if same_workload else weakest([tokens_label, caps_label, "ESTIMATE"])},
           "how": "energy ratio = token ratio x J-per-token ratio (the layers act on different factors)"}
    if not same_workload:
        row["energy_saved_pct"]["note"] = ("each layer is MEASURED on its own workload; the stacked "
                                           "figure assumes both apply to one workload, so it is an ESTIMATE")
    if usd_per_mtok is not None:
        row["usd_saved_pct"] = {"value": round(100 * (1 - t), 2), "label": tokens_label,
                                "note": "per-token billing: dollars follow tokens; caps do not change a hosted bill"}
    return row
