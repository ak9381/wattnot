"""The open-weights planner on Baseten, behind its own spend guard.

`BasetenClient` is a `client(body, purpose=...) -> dict` callable with the same
shape `MusePlanner` already reads (`usage_of` / `_json`): it POSTs to
`{base_url}/chat/completions` with the key from `BASETEN_API_KEY`, priced from
the reply's usage at the published rates. `OpenWeightsPlanner` is a
`MusePlanner` that talks through it under the name `muse-spark-open`.

The Baseten spend ledger here is separate from the Meta ledger in
`wattnot.meta.ledger`: different provider, different cap, different file.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from wattnot.cloud import BASE_URL, KEY_ENV, MODEL
from wattnot.meta.agent import _body, _json, usage_of
from wattnot.proof.planner import (
    SCHEMA,
    BisectPlanner,
    MusePlanner,
    allowed_caps,
    build_prompt,
    clean_explanation,
)

MIN_MAX_TOKENS = 4000  # the model spends 1,000-1,500 tokens reasoning on a tiny task
IN_PER_M = 1.25
CACHED_PER_M = 0.15
OUT_PER_M = 4.25
LEDGER_NAME = "baseten-ledger.json"
PROVIDER = "baseten"
log = logging.getLogger("wattnot.planner.spark_open")


def prompt_sha256(system: str, payload) -> str:
    """Hash of exactly what we sent (system + payload), so a receipt can name the prompt
    without carrying it."""
    blob = json.dumps({"system": system, "payload": payload}, sort_keys=True,
                      separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


class BasetenError(RuntimeError):
    """A Baseten call problem. The message never contains the key."""


class CapReached(BasetenError):
    """The call would take Baseten spend over the cap."""


def price_usd(input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
    """USD for one call at the published rates (same numbers as Muse Spark)."""
    cached = max(0, min(int(cached_tokens), int(input_tokens)))
    usd = ((int(input_tokens) - cached) * IN_PER_M + cached * CACHED_PER_M
           + int(output_tokens) * OUT_PER_M) / 1_000_000
    return round(usd, 8)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _default_transport(req: Mapping) -> dict:
    """POST one chat-completions body with urllib. `req` carries url, body and
    timeout only: the key is read here, used in one header, and never returned."""
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        raise BasetenError(f"{KEY_ENV} is not set. Export it in your shell; it is never "
                           "read from a file or a flag.")
    data = json.dumps(req["body"]).encode()
    r = urllib.request.Request(
        req["url"], data=data,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(r, timeout=req.get("timeout_s", 120)) as resp:  # noqa: S310
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read() or b"{}")
        except ValueError:
            err = {}
        msg = (err.get("error") or {}).get("message") if isinstance(err, dict) else ""
        raise BasetenError(f"HTTP {e.code}: {str(msg)[:160]}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise BasetenError(f"network error: {type(e).__name__}") from None


class BasetenClient:
    """`client(body, purpose=...)` -> the OpenAI-style response dict.

    The body always goes out with this client's own model; any other model in
    the body is refused, never silently replaced. `transport` is an injectable
    `req -> response dict` function so tests never touch the network.
    """

    def __init__(self, model: str = MODEL, base_url: str = BASE_URL,
                 ledger_path: str | Path | None = None, cap_usd: float = 1.0,
                 transport: Callable[[dict], dict] | None = None,
                 timeout_s: float = 120) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.ledger_path = Path(ledger_path) if ledger_path is not None else None
        self.cap_usd = float(cap_usd)
        self.transport = transport or _default_transport
        self.timeout_s = timeout_s
        self._memory: list[dict] = []

    def __repr__(self) -> str:  # the key is never stored, so it cannot leak here
        return f"BasetenClient(model={self.model!r}, base_url={self.base_url!r})"

    # ---- the ledger (this provider only) -------------------------------------
    def _read(self) -> list[dict]:
        if self.ledger_path is None:
            return list(self._memory)
        if not self.ledger_path.is_file():
            return []
        doc = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        entries = doc.get("entries")
        return list(entries) if isinstance(entries, list) else []

    def _write(self, entries: list[dict]) -> None:
        if self.ledger_path is None:
            self._memory = list(entries)
            return
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"provider": self.provider, "entries": entries}
        self.ledger_path.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")

    def total_usd(self) -> float:
        return round(sum(e.get("usd", 0.0) for e in self._read()), 6)

    @property
    def entries(self) -> list[dict]:
        return self._read()

    def _estimate_usd(self, body: Mapping) -> float:
        n_in = math.ceil(len(json.dumps(body, separators=(",", ":"))) / 3.0)
        n_out = int(body.get("max_tokens") or body.get("max_completion_tokens")
                    or MIN_MAX_TOKENS)
        return price_usd(n_in, n_out)

    # ---- the call --------------------------------------------------------------
    provider = PROVIDER

    def _check_key(self) -> None:
        if not os.environ.get(KEY_ENV, "").strip():
            raise BasetenError(f"{KEY_ENV} is not set. Export it in your shell; it is "
                               "never read from a file or a flag.")

    def __call__(self, body: Mapping, purpose: str = "unspecified") -> dict:
        # The key is read here, at call time, from the environment only. An
        # injected transport never sees it; only the default transport puts it
        # in one header, and no error, ledger row or repr ever contains it.
        self._check_key()
        body = dict(body)
        if body.get("model") not in (None, self.model):
            raise BasetenError(f"refusing to send model {body.get('model')!r}; "
                               f"this client only uses {self.model}")
        body["model"] = self.model
        if not (body.get("max_tokens") or body.get("max_completion_tokens")):
            body["max_tokens"] = MIN_MAX_TOKENS
        est = self._estimate_usd(body)
        spent = self.total_usd()
        if spent + est > self.cap_usd:
            raise CapReached(f"refused: ${spent:.4f} spent + ${est:.4f} worst case "
                             f"for this call > ${self.cap_usd:.2f} cap")
        req = {"url": f"{self.base_url}/chat/completions", "body": body,
               "timeout_s": self.timeout_s, "purpose": purpose}
        try:
            resp = self.transport(req)
        except (BasetenError, CapReached):
            raise
        except Exception as e:  # noqa: BLE001 - transport problems carry no key
            raise BasetenError(f"request failed: {type(e).__name__}") from None
        if not isinstance(resp, dict):
            raise BasetenError("the Baseten reply was not a JSON object")
        usage = resp.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        row = {"ts": _now(), "model": self.model, "purpose": purpose,
               "input_tokens": int(usage.get("prompt_tokens", 0)),
               "cached_tokens": int(details.get("cached_tokens", 0) or 0),
               "output_tokens": int(usage.get("completion_tokens", 0)),
               "usd": price_usd(int(usage.get("prompt_tokens", 0)),
                                int(usage.get("completion_tokens", 0)),
                                int(details.get("cached_tokens", 0) or 0)),
               "label": "MEASURED", "provider": self.provider}
        entries = self._read()
        entries.append(row)
        self._write(entries)
        return resp


class OpenWeightsPlanner(MusePlanner):
    """Muse Spark Open on Baseten proposes the next cap. Same allow-list and
    bisect fallback as `MusePlanner`; the answer is tagged `muse-spark-open`."""

    name = "muse-spark-open"
    partner = "baseten"

    def __init__(self, client: Callable[..., dict], *, lo: int = 300, hi: int = 700,
                 fallback: BisectPlanner | None = None,
                 model: str = MODEL) -> None:
        super().__init__(client, lo=lo, hi=hi, fallback=fallback, model=model)

    def propose(self, history) -> dict:
        """Same prompt, allow-list and fallback as `MusePlanner`, priced at the
        Baseten rates and answered under this planner's own name and model."""
        free = allowed_caps(self.lo, self.hi, [h["cap_w"] for h in history])
        if not free:
            return {"kind": "stop", "cap_w": None, "reason": "every allowed cap was tried",
                    "by": self.name}
        system, payload = build_prompt(history, self.lo, self.hi, self.context)
        call = {"provider": getattr(self.client, "provider", self.partner), "model": self.model,
                "prompt_sha256": prompt_sha256(system, payload),
                "latency_ms": None, "label": "MEASURED", "fallback": False,
                "fallback_reason": None}
        self.last_call = call
        t0 = time.perf_counter()
        try:
            raw = self.client(_body(system, payload, SCHEMA, "NextCap", effort="low",
                                    max_tokens=4000, model=self.model),
                              purpose="next-power-cap")
            call["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            usage = usage_of(raw)
            self.last_usage, self.last_error = usage, None
        except Exception as e:  # noqa: BLE001 - no answer came back, so bisect picks
            self.last_error = f"{type(e).__name__}: {str(e)[:160]}"
            call.update(latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                        fallback=True, fallback_reason=f"call failed: {self.last_error}")
            log.warning("FALLBACK: Muse Spark Open on Baseten gave no answer (%s); "
                        "the deterministic bisect rule picks this cap", self.last_error)
            out = self.fallback.propose(history)
            return {**out, "by": "bisect-fallback", "muse_error": self.last_error,
                    "call": call, "proposal_accepted": None, "explanation": None, "usage": None,
                    "model": self.model, "usd": None}
        try:
            d = _json(raw)
            reason = " ".join(str(d.get("reason", "")).split())[:240]
            explanation = clean_explanation(d.get("explanation"))
            usage = dict(usage)
            usd = round(price_usd(usage.get("input", 0), usage.get("output", 0)), 6)
            if d.get("kind") == "stop":
                return {"kind": "stop", "cap_w": None, "reason": reason, "by": self.name,
                        "proposal_accepted": True, "explanation": explanation,
                        "usage": usage, "model": self.model, "usd": usd, "call": call}
            if d.get("cap_w") not in free:
                raise ValueError(f"off the allow-list: {d.get('cap_w')!r}")
            return {"kind": "try", "cap_w": int(d["cap_w"]), "reason": reason, "by": self.name,
                    "proposal_accepted": True, "explanation": explanation,
                    "usage": usage, "model": self.model, "usd": usd, "call": call}
        except Exception as e:  # noqa: BLE001 - the answer failed validation, so bisect picks
            self.last_error = f"{type(e).__name__}: {str(e)[:160]}"
            call.update(fallback=True, fallback_reason=f"answer rejected: {self.last_error}")
            log.warning("FALLBACK: Muse Spark Open answer failed validation (%s); "
                        "the deterministic bisect rule picks this cap", self.last_error)
            out = self.fallback.propose(history)
            return {**out, "by": "bisect-fallback", "muse_error": self.last_error,
                    "call": call, "proposal_accepted": False, "explanation": None, "usage": None,
                    "model": self.model, "usd": None}


__all__ = ["BasetenClient", "BasetenError", "CapReached", "OpenWeightsPlanner",
           "LEDGER_NAME", "MIN_MAX_TOKENS", "PROVIDER", "price_usd", "prompt_sha256"]
