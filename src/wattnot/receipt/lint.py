"""Receipt linter: numbers, ids and settings only, because Rekor is public.

Rejects prompt or output text fields anywhere (a number such as
`tokens.output` is fine), and any string longer than
200 characters except `planner_trace[].reason`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

BANNED_KEYS = {"prompt", "prompts", "output", "outputs", "completion", "completions",
               "messages", "response_text"}
MAX_STR = 200
MAX_REASON = 600


def lint(receipt: Mapping) -> list[str]:
    errors: list[str] = []

    def walk(v: Any, path: str) -> None:
        if isinstance(v, Mapping):
            for k, x in v.items():
                if k in BANNED_KEYS and not isinstance(x, (int, float)):
                    errors.append(f"{path}.{k}: text field '{k}' is not allowed")
                walk(x, f"{path}.{k}")
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, f"{path}[{i}]")
        elif isinstance(v, str):
            is_reason = path.startswith("$.planner_trace[") and path.endswith(".reason")
            limit = MAX_REASON if is_reason else MAX_STR
            if len(v) > limit:
                errors.append(f"{path}: string of {len(v)} chars (max {limit})")

    walk(receipt, "$")
    return errors
