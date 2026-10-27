"""One chat request to a vLLM (or any OpenAI-compatible) server, timed. Temperature 0."""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass


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
        return Reply(lat, int((data.get("usage") or {}).get("completion_tokens", 0)),
                     (ch.get("message") or {}).get("content") or "",
                     ch.get("finish_reason") or "stop")
