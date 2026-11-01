"""Ask about a dashboard card: Muse Spark explains the card the user picked, as text only.

The browser sends the card's id, title and its visible text (trimmed). Spark answers in a few
plain sentences and names the part of the card it is explaining (`focus`), which the dashboard
highlights. No spoken output. Live by default through the guarded client (spend cap, ledger);
WATTNOT_META_MOCK=1 returns a canned answer labelled MOCK, kept out of the real ledger.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

MAX_TEXT = 2500
SYSTEM = ("You explain one card on the WattNot dashboard (an agent that cuts LLM inference energy "
          "and cost without hurting quality). Use only the card text given. Say what the card shows, "
          "what its main number means, and keep each number's label (MEASURED, ESTIMATE, SIMULATED, "
          "SYNTHETIC, REPLAY, MOCK, PLANNED). 2-4 short sentences, plain words, no claims beyond the "
          "card. `focus` = a short exact phrase copied from the card text that the explanation is about.")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["explanation", "focus"],
          "properties": {"explanation": {"type": "string"}, "focus": {"type": "string"}}}


def _clean(s: Any, n: int) -> str:
    return " ".join(str(s or "").split())[:n]


def request_body(title: str, text: str) -> dict:
    return {"model": os.environ.get("WATTNOT_SPARK_MODEL", "muse-spark-1.3"),
            "reasoning_effort": "minimal", "max_tokens": 700,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": f"Card: {title}\n\n{text}"}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "CardExplanation", "strict": True, "schema": SCHEMA}}}


def _mock_opener(req, timeout):   # canned answer, kept out of the real ledger
    body = json.loads(req.data)
    title = body["messages"][1]["content"].split("\n", 1)[0].removeprefix("Card: ")
    msg = {"explanation": f"[MOCK] A canned answer about {title}, not written by Muse Spark.",
           "focus": title}
    out = {"id": "mock-askcard", "choices": [{"message": {"content": json.dumps(msg)}}],
           "usage": {"prompt_tokens": 200, "completion_tokens": 40}}
    return 200, json.dumps(out).encode()


def explain_card(card_id: str, title: str, text: str, *, mock: bool | None = None,
                 client: Any = None) -> dict:
    from wattnot.meta.client import GuardedClient, load_key, meta_mock_on

    card_id, title, text = _clean(card_id, 80), _clean(title, 160), _clean(text, MAX_TEXT)
    if not text:
        return {"card_id": card_id, "label": "ERROR", "code": "empty_card",
                "explanation": "That card has no text to explain yet.", "focus": ""}
    mock = meta_mock_on() if mock is None else mock
    if client is None:
        if mock:
            from wattnot.meta.ledger import SpendLedger
            led = SpendLedger(Path(tempfile.mkdtemp(prefix="askcard-mock-")) / "ledger.json")
            client = GuardedClient(opener=_mock_opener, key="mock", ledger=led)
        else:
            if not load_key():
                return {"card_id": card_id, "label": "ERROR", "code": "no_key",
                        "explanation": "Live Ask needs a Meta API key. No explanation was generated.", "focus": ""}
            client = GuardedClient(timeout_s=40)
    data = client(request_body(title, text), purpose=f"askcard:{card_id or 'card'}")
    from wattnot.meta.spark_trace import _parse
    got = _parse(data["choices"][0]["message"]["content"]) or {}
    focus = _clean(got.get("focus"), 200)
    return {"card_id": card_id, "label": "MOCK" if mock else "LIVE",
            "model": request_body("", "")["model"],
            "explanation": _clean(got.get("explanation"), 1200),
            "focus": focus if focus and focus.lower() in text.lower() else ""}
