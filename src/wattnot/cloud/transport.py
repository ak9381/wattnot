"""One call, three transports: live HTTP, a recorded fixture, and a recorder.

The tuner never builds HTTP itself. It makes a `Request` (a tag, a body, some
headers) and hands it to a transport that returns a `Reply`. Live runs use
`LiveTransport`. Offline runs use `FixtureTransport`, which looks the reply up
by tag. `Recorder` wraps a live transport and writes a fixture that can be
replayed later.

The API key comes only from the BASETEN_API_KEY environment variable. It is
sent in one header and is scrubbed from every error text. It is never logged,
written to a fixture or put in a receipt.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from wattnot.cloud import BASE_URL, KEY_ENV, MODEL

FIXTURE_FORMAT = "wattnot.cloud-fixture/1"
RATE_HEADERS = ("x-ratelimit-limit-requests", "x-ratelimit-remaining-requests",
                "x-ratelimit-limit-tokens", "x-ratelimit-remaining-tokens")
RETRY_STATUS = (429, 529)


class CloudError(RuntimeError):
    """A transport problem. The message never contains the key."""


class MissingKey(CloudError):
    pass


@dataclass(frozen=True)
class Request:
    tag: str  # stable id, e.g. "logic-2|high|0"; fixtures are keyed by it
    body: dict
    headers: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Reply:
    status: int
    body: dict
    latency_s: float
    headers: dict = field(default_factory=dict)  # rate-limit headers only
    retries: int = 0
    sent_at: float = 0.0  # unix seconds


Transport = Callable[[Request], Reply]


def scrub(text: str, key: str | None) -> str:
    return text.replace(key, "[redacted]") if key else text


def read_env(name: str, env: Mapping[str, str] | None = None) -> str:
    """The one place the environment is read."""

    return (env if env is not None else os.environ).get(name, "").strip()


def read_key(env: Mapping[str, str] | None = None) -> str:
    key = read_env(KEY_ENV, env)
    if not key:
        raise MissingKey(f"{KEY_ENV} is not set. Export it in your shell; it is never read from "
                         "a file or a flag.")
    return key


def key_prefix(key: str) -> str | None:
    """Baseten lists keys by the part before the first dot. None if the key has no dot."""

    head, dot, _ = key.partition(".")
    return head if dot and head else None


class LiveTransport:
    def __init__(self, *, base_url: str = BASE_URL, timeout_s: float = 600,
                 max_retries: int = 4, sleep: Callable[[float], None] = time.sleep,
                 env: Mapping[str, str] | None = None, min_interval_s: float = 4.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._key = read_key(env)
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._sleep = sleep
        # An unverified account gets about 15 requests a minute: space calls this far apart.
        self.min_interval_s = min_interval_s
        self._clock = clock
        self._last_start: float | None = None

    def _throttle(self) -> None:
        now = self._clock()
        if self._last_start is not None:
            wait = self._last_start + self.min_interval_s - now
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
        self._last_start = now

    def __repr__(self) -> str:  # never show the key
        return f"LiveTransport(base_url={self.base_url!r})"

    @property
    def api_key_prefix(self) -> str | None:
        return key_prefix(self._key)

    def __call__(self, req: Request) -> Reply:
        if req.body.get("model") != MODEL:
            raise CloudError(f"refusing to send model {req.body.get('model')!r}; "
                             f"this key only works with {MODEL}")
        data = json.dumps(req.body).encode()
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json",
                   **req.headers}
        retries = 0
        while True:
            self._throttle()
            t0 = time.time()
            status, raw, hdrs = self._once(data, headers)
            latency = time.time() - t0
            if status in RETRY_STATUS and retries < self.max_retries:
                wait = _retry_wait(hdrs, retries)
                retries += 1
                self._sleep(wait)
                continue
            try:
                body = json.loads(raw) if raw else {}
            except ValueError:
                body = {"error": {"message": scrub(raw[:200].decode("utf-8", "replace"),
                                                   self._key)}}
            rate = {h: hdrs[h] for h in RATE_HEADERS if h in hdrs}
            return Reply(status, body, latency, rate, retries, t0)

    def _once(self, data: bytes, headers: dict) -> tuple[int, bytes, dict]:
        r = urllib.request.Request(f"{self.base_url}/chat/completions", data=data,
                                   headers=headers, method="POST")
        try:
            with urllib.request.urlopen(r, timeout=self.timeout_s) as resp:  # noqa: S310
                return resp.status, resp.read(), {k.lower(): v for k, v in resp.headers.items()}
        except urllib.error.HTTPError as e:
            return e.code, e.read(), {k.lower(): v for k, v in e.headers.items()}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise CloudError(scrub(f"network error: {type(e).__name__}: {e}", self._key)) from None


def _retry_wait(headers: Mapping[str, str], attempt: int) -> float:
    try:
        return min(60.0, float(headers.get("retry-after", "")))
    except ValueError:
        return min(30.0, (2 ** attempt) + random.uniform(0, 1))  # noqa: S311 - jitter only


# ---- fixtures -------------------------------------------------------------------------------

def _strip_reasoning(body: dict) -> dict:
    """Keep the answer, drop the reasoning text; store its length and hash instead."""

    out = json.loads(json.dumps(body))
    for ch in out.get("choices") or []:
        msg = ch.get("message") or {}
        for k in ("reasoning_content", "reasoning"):
            text = msg.pop(k, None)
            if isinstance(text, str) and text:
                msg["reasoning_chars"] = len(text)
                msg["reasoning_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    return out


class Recorder:
    """Wraps a transport and keeps every reply for `save()`."""

    def __init__(self, inner: Transport, *, source: str = "recorded") -> None:
        self.inner = inner
        self.source = source
        self.replies: dict[str, dict] = {}

    def __call__(self, req: Request) -> Reply:
        r = self.inner(req)
        self.replies[req.tag] = {"status": r.status, "body": _strip_reasoning(r.body),
                                 "latency_s": round(r.latency_s, 4), "headers": r.headers,
                                 "retries": r.retries}
        return r

    def save(self, path: str | Path, **extra) -> Path:
        doc = {"format": FIXTURE_FORMAT, "source": self.source, "model": MODEL,
               "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **extra,
               "replies": self.replies}
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return p


class FixtureTransport:
    """Offline replay. A tag that is not in the fixture is an error, never a guess."""

    def __init__(self, doc: Mapping) -> None:
        if doc.get("format") != FIXTURE_FORMAT:
            raise CloudError(f"not a cloud fixture (format {doc.get('format')!r})")
        self.doc = doc
        self._t = 1_790_000_000.0  # fake clock so replay timestamps are stable

    @classmethod
    def load(cls, path: str | Path) -> FixtureTransport:
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    @property
    def source(self) -> str:
        return self.doc.get("source", "synthetic")

    def __call__(self, req: Request) -> Reply:
        rec = (self.doc.get("replies") or {}).get(req.tag)
        if rec is None:
            raise CloudError(f"fixture has no reply for {req.tag!r}")
        self._t += float(rec["latency_s"])
        return Reply(int(rec["status"]), rec["body"], float(rec["latency_s"]),
                     rec.get("headers") or {}, int(rec.get("retries", 0)), self._t)
