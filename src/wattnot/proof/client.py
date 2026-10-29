"""One chat request to a vLLM (or any OpenAI-compatible) server, timed. Temperature 0."""

from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass

# A reasoning model (Muse Glimmer, served with --reasoning-parser) returns its thinking in a
# separate `reasoning_content` field, leaving `content` as the final answer. But if the parser
# fails to initialise (RUN1 warned "Auto-initialization of reasoning token IDs failed"), the
# thinking leaks into `content` wrapped in <think>...</think>, and the fixed checks then grade the
# chain of thought instead of the answer (RUN1's ~6% baseline). Strip any such block so the gate
# always sees the answer. No-op when the parser works (no tags) or on a non-reasoning model; an
# unterminated block (thinking cut off by max_tokens) is stripped to empty, which the token-limit
# rule already fails. Mirrors wattnot.longrun.harness.Chat.
_THINK = re.compile(r"<think>.*?(</think>|$)", re.S)


def strip_reasoning(text: str) -> str:
    """Remove a leaked <think>...</think> block; return the final answer, stripped."""
    return _THINK.sub("", text).strip()


@dataclass(frozen=True)
class Reply:
    latency_s: float
    tokens: int
    text: str
    finish: str  # "stop", "length" or "error"


class ChatClient:
    def __init__(self, base_url: str, model: str, *, timeout_s: float = 300.0) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.timeout_s = timeout_s

    def ask(self, prompt: str, max_tokens: int) -> Reply:
        body = json.dumps({"model": self.model, "temperature": 0, "max_tokens": max_tokens,
                           # pin greedy even if a model's generation_config sets top_k/temp
                           # (Glimmer ships temperature 1.0, top_k 64, top_p 0.95)
                           "top_k": -1, "top_p": 1.0, "seed": 0,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:  # noqa: S310
                data = json.loads(r.read())
        except Exception as e:  # noqa: BLE001 - a failed request is recorded, not hidden
            return Reply(time.perf_counter() - t0, 0, f"[request failed: {type(e).__name__}]",
                         "error")
        lat = time.perf_counter() - t0
        ch = (data.get("choices") or [{}])[0]
        content = (ch.get("message") or {}).get("content") or ""
        return Reply(lat, int((data.get("usage") or {}).get("completion_tokens", 0)),
                     strip_reasoning(content),
                     ch.get("finish_reason") or "stop")
