"""One guarded SAM 3.1 call: a photo and a noun phrase in, SAM's raw output line out.

Every call is written to the spend ledger first (a per-image price) and refused over the
cap. The key is read the same way as for the other Meta calls and is never printed or
stored. Raw output is returned as text so the caller can save it and replay it offline.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable

from wattnot.meta.client import DEFAULT_BASE, MetaError, load_key
from wattnot.meta.ledger import BudgetExceeded, SpendLedger

MODEL = "sam-3.1"

Opener = Callable[[urllib.request.Request, float], tuple[int, bytes]]


def _urlopen(req: urllib.request.Request, timeout: float) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b"{}"


def request_body(prompt: str, image_b64: str, mime: str = "image/jpeg") -> dict:
    return {"model": MODEL, "input": [{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": prompt},
        {"type": "input_image", "image_url": f"data:{mime};base64,{image_b64}"}]}]}


def output_text(data: dict) -> str:
    """The concatenated output text of a /v1/responses reply."""

    parts = []
    for item in data.get("output", []):
        for c in item.get("content", []):
            if c.get("type") == "output_text":
                parts.append(c.get("text", ""))
    return "".join(parts)


class SamClient:
    def __init__(self, *, key: str | None = None, base: str | None = None,
                 ledger: SpendLedger | None = None, opener: Opener | None = None,
                 timeout_s: float = 180) -> None:
        self.key = key if key is not None else load_key()
        self.base = (base or DEFAULT_BASE).rstrip("/")
        self.ledger = ledger or SpendLedger()
        self.opener = opener or _urlopen
        self.timeout_s = timeout_s

    def segment(self, image: bytes, prompt: str, *, purpose: str = "sam:rack-audit",
                mime: str = "image/jpeg") -> str:
        if not self.key:
            raise MetaError("no API key: set META_API_KEY or log in with `muse login`")
        eid = self.ledger.charge_image(MODEL, purpose, note=prompt)  # raises BudgetExceeded
        body = request_body(prompt, base64.b64encode(image).decode(), mime)
        req = urllib.request.Request(
            f"{self.base}/responses", data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        t0 = time.monotonic()
        try:
            status, raw = self.opener(req, self.timeout_s)
        except Exception as e:  # may still have been billed: keep the flat charge
            raise MetaError(f"request failed: {type(e).__name__}") from None
        if status != 200:
            self.ledger.refund(eid, f"HTTP {status}")
            raise MetaError(f"HTTP {status}", status)
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = {}
        self.last_latency_s = round(time.monotonic() - t0, 2)
        return output_text(data)


__all__ = ["BudgetExceeded", "MODEL", "SamClient", "output_text", "request_body"]
