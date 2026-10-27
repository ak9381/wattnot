"""Chat Completions client for the Meta Model API that always goes through the spend guard.

The key comes from META_API_KEY, or, when that is unset, from the Muse login file
(~/.config/muse/auth.json, provider `meta`). It is only ever held in memory: it is not
printed, logged, put in the ledger or written to any receipt.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import warnings
from collections.abc import Callable, Mapping
from pathlib import Path

from wattnot.meta.ledger import DEFAULT_MAX_OUTPUT, BudgetExceeded, SpendLedger

DEFAULT_BASE = "https://api.meta.ai/v1"
AUTH_FILE = Path("~/.config/muse/auth.json")
KEY_ENV = "META_API_KEY"

Opener = Callable[[urllib.request.Request, float], tuple[int, bytes]]


class MetaError(RuntimeError):
    def __init__(self, msg: str, status: int | None = None) -> None:
        super().__init__(msg)
        self.status = status


def load_key(auth_file: str | Path | None = None) -> str:
    """META_API_KEY if set, else the Muse login file. Returns '' when neither has one."""

    key = os.environ.get(KEY_ENV, "").strip()
    if key:
        return key
    try:
        p = Path(auth_file or os.environ.get("WATTNOT_MUSE_AUTH") or AUTH_FILE).expanduser()
        meta = json.loads(p.read_text())["providers"]["meta"]
        return str(meta.get("api_key") or "").strip()
    except (OSError, KeyError, TypeError, ValueError):
        return ""


def _urlopen(req: urllib.request.Request, timeout: float) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b"{}"


class GuardedClient:
    """`client(body, purpose=...)` -> parsed response. Refuses over the cap, logs every call."""

    def __init__(self, *, key: str | None = None, base: str | None = None,
                 ledger: SpendLedger | None = None, opener: Opener | None = None,
                 timeout_s: float = 120) -> None:
        self.key = key if key is not None else load_key()
        self.base = (base or os.environ.get("META_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self.ledger = ledger or SpendLedger()
        self.opener = opener or _urlopen
        self.timeout_s = timeout_s

    def __call__(self, body: Mapping, *, purpose: str = "unspecified") -> dict:
        if not self.key:
            raise MetaError(f"no API key: set {KEY_ENV} or log in with `muse login`")
        body = dict(body)
        if not (body.get("max_tokens") or body.get("max_completion_tokens")):
            body["max_completion_tokens"] = DEFAULT_MAX_OUTPUT  # the guard prices this cap
        eid, soft = self.ledger.reserve(body, purpose)  # raises BudgetExceeded
        if soft:
            warnings.warn(f"Meta spend is over the ${self.ledger.soft_usd:.0f} soft limit "
                          f"(cap ${self.ledger.cap_usd:.0f})", stacklevel=2)
        req = urllib.request.Request(
            f"{self.base}/chat/completions", data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        t0 = time.monotonic()
        try:
            status, raw = self.opener(req, self.timeout_s)
        except Exception as e:  # timeout or network: the call may still have been billed
            self.ledger.settle(eid, None, status="unknown", note=type(e).__name__,
                               latency_s=time.monotonic() - t0)
            raise MetaError(f"request failed: {type(e).__name__}") from None
        lat = time.monotonic() - t0
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = {}
        if status != 200:
            err = data.get("error") if isinstance(data, dict) else None
            msg = err.get("message") if isinstance(err, dict) else str(err or "")
            self.ledger.settle(eid, None, status="error", http_status=status, latency_s=lat,
                               note=msg)
            raise MetaError(f"HTTP {status}: {msg[:160]}", status)
        self.ledger.settle(eid, data.get("usage"), model=str(body.get("model", "")),
                           http_status=status, latency_s=lat,
                           status="ok" if data.get("usage") else "ok-no-usage")
        return data


__all__ = ["BudgetExceeded", "GuardedClient", "MetaError", "load_key"]
