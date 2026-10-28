"""Run the WattNot proxy as a drop-in OpenAI base URL, for framework tests and examples.

``mock_upstream`` is a tiny OpenAI-shaped model with no GPU and no network. Everything it
returns is MOCK. The small tier "fails" (refuses) on prompts that contain the word HARD, so
small-model-first routing has something to escalate.
"""

from __future__ import annotations

import socket
import threading
import time

from wattnot.agentwaste.config import WasteConfig
from wattnot.agentwaste.server import create_app
from wattnot.context_saver import ContextSaver
from wattnot.route.escalate import TIER_CHAIN


def _last_user(messages) -> str:
    for m in reversed(messages or []):
        if m.get("role") == "user":
            c = m.get("content")
            return c if isinstance(c, str) else str(c)
    return ""


def mock_upstream(body: dict, headers: dict) -> dict:
    model = str(body.get("model", ""))
    prompt = _last_user(body.get("messages"))
    small = model == TIER_CHAIN[0]["model"]
    if small and "HARD" in prompt:
        text = "Sorry, I can't help with that."
    else:
        text = f"Answer from {model.split('/')[-1] or 'model'}: the receipts live in wattnot.receipt."
    p_tok = sum(len(str(m.get("content") or "")) for m in body.get("messages") or []) // 4 + 1
    return {"id": "mock-1", "object": "chat.completion", "created": 0, "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": p_tok, "completion_tokens": len(text) // 4,
                      "total_tokens": p_tok + len(text) // 4}}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    if port in (8765, 8770):  # demo servers stay untouched
        return free_port()
    return port


def serve_mock(port: int | None = None):
    """Start the full proxy (waste guard + context saver + small-first + gate) on a free port.

    Returns (base_url, server). Call ``server.should_exit = True`` to stop it.
    """
    import uvicorn

    port = port or free_port()
    app = create_app(mock_upstream, WasteConfig(), context_saver=ContextSaver(),
                     tiers=TIER_CHAIN, label="MOCK")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}/v1", server
