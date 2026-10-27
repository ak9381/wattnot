"""Cache-friendly prompt layout, and session affinity.

Baseten caches a prompt prefix automatically and bills the cached part at a
lower rate. A cache hit needs the prefix to match exactly, so:

* put what never changes first (system text, tool list, shared documents),
* put what changes last (the task, the user turn),
* keep timestamps, request ids and other random values out of the stable part.

`x-session-affinity` sends related calls to the same replica, where the cache
already lives. Use one opaque id per agent task and a new one per unrelated task.
Affinity only steers routing; the full message list is still sent each time.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Sequence

AFFINITY_HEADER = "x-session-affinity"

# Things that change on every call and silently break a prefix match.
VOLATILE = (
    ("timestamp", re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?")),
    ("unix time", re.compile(r"\b1[6-9]\d{8}(\d{3})?\b")),
    ("uuid", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)),
    ("request id", re.compile(r"\b(req|request|trace)[-_ ]?id[:= ]+\S+", re.I)),
)


def build_messages(*, system: str = "", tools: str = "", shared: Sequence[str] = (),
                   task: str) -> list[dict]:
    """Stable parts first, in a fixed order; the task last. Empty parts are skipped."""

    stable = "\n\n".join(p.strip() for p in (system, tools, *shared) if p and p.strip())
    msgs = []
    if stable:
        msgs.append({"role": "system", "content": stable})
    msgs.append({"role": "user", "content": task})
    return msgs


def stable_prefix(messages: Sequence[dict]) -> str:
    """Everything before the last message."""

    return "\n".join(f"{m['role']}:{m['content']}" for m in messages[:-1])


def prefix_hash(messages: Sequence[dict]) -> str:
    return hashlib.sha256(stable_prefix(messages).encode()).hexdigest()[:16]


def volatile_in_prefix(messages: Sequence[dict]) -> list[str]:
    """Names of volatile patterns found in the part that should be stable."""

    text = stable_prefix(messages)
    return [name for name, rx in VOLATILE if rx.search(text)]


def new_session_id() -> str:
    return str(uuid.uuid4())


def affinity_headers(session_id: str | None) -> dict:
    return {AFFINITY_HEADER: session_id} if session_id else {}


def hashed(session_id: str | None) -> str | None:
    """Receipts keep a hash of the id, not the id."""

    return hashlib.sha256(session_id.encode()).hexdigest()[:12] if session_id else None


def cached_share(prompt_tokens: int, cached_tokens: int) -> float:
    return round(cached_tokens / prompt_tokens, 4) if prompt_tokens else 0.0
