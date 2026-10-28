"""Modal as a second host for the open-weights planner, next to Baseten.

Modal Endpoints serve open models (Meta's Llama and others) behind an
OpenAI-compatible `/v1/chat/completions`, so `ModalClient` is the Baseten
client with a different URL, a different credential and its own ledger rows
(`provider: "modal"`). `ModalOpenPlanner` is the same planner, same prompt,
allow-list and bisect fallback, so receipts carry the same `call` fields
(provider, model id, prompt hash, latency, fallback).

Credentials, env only, never printed:
- `MODAL_ENDPOINT_URL`: the endpoint base URL (ending in `/v1` or not).
- `MODAL_PROXY_TOKEN_ID` + `MODAL_PROXY_TOKEN_SECRET`: a workspace Proxy Token,
  sent as `Authorization: Bearer <id>.<secret>`. These are not the account
  tokens in `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET`, which drive the CLI
  (deploy, list apps) and are never sent to an endpoint.
- `MODAL_MODEL`: the model name the endpoint serves (default below).

Price: Shared Endpoints bill per token and Dedicated ones bill GPU time, and we
have no published per-token rate on file, so `usd` stays an ESTIMATE from the
Baseten list price until a Modal bill says otherwise.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path

from wattnot.proof.baseten_planner import BasetenClient, BasetenError, OpenWeightsPlanner

PROVIDER = "modal"
URL_ENV = "MODAL_ENDPOINT_URL"
ID_ENV = "MODAL_PROXY_TOKEN_ID"
SECRET_ENV = "MODAL_PROXY_TOKEN_SECRET"
MODEL_ENV = "MODAL_MODEL"
DEFAULT_MODEL = "meta-llama/Llama-3.1-8B-Instruct"
LEDGER_NAME = "modal-ledger.json"


class ModalError(BasetenError):
    """A Modal call problem. The message never contains a token."""


def base_url_from_env() -> str:
    url = os.environ.get(URL_ENV, "").strip().rstrip("/")
    if not url:
        raise ModalError(f"{URL_ENV} is not set (the Modal Endpoint URL).")
    return url if url.endswith("/v1") else url + "/v1"


def _default_transport(req: Mapping) -> dict:
    tid, sec = os.environ.get(ID_ENV, "").strip(), os.environ.get(SECRET_ENV, "").strip()
    if not (tid and sec):
        raise ModalError(f"{ID_ENV} and {SECRET_ENV} must both be set (a Modal Proxy Token).")
    r = urllib.request.Request(
        req["url"], data=json.dumps(req["body"]).encode(),
        headers={"Authorization": f"Bearer {tid}.{sec}", "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(r, timeout=req.get("timeout_s", 120)) as resp:  # noqa: S310
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise ModalError(f"HTTP {e.code} from the Modal endpoint") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ModalError(f"network error: {type(e).__name__}") from None


class ModalClient(BasetenClient):
    """`client(body, purpose=...)` against a Modal Endpoint; same ledger and cap guard."""

    provider = PROVIDER

    def __init__(self, model: str | None = None, base_url: str | None = None,
                 ledger_path: str | Path | None = None, cap_usd: float = 1.0,
                 transport: Callable[[dict], dict] | None = None,
                 timeout_s: float = 120) -> None:
        super().__init__(model=model or os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL,
                         base_url=base_url or base_url_from_env(), ledger_path=ledger_path,
                         cap_usd=cap_usd, transport=transport or _default_transport,
                         timeout_s=timeout_s)

    def __repr__(self) -> str:
        return f"ModalClient(model={self.model!r}, base_url={self.base_url!r})"

    def _check_key(self) -> None:
        if not (os.environ.get(ID_ENV, "").strip() and os.environ.get(SECRET_ENV, "").strip()):
            raise ModalError(f"{ID_ENV} and {SECRET_ENV} must both be set (a Modal Proxy "
                             "Token). They are never read from a file or a flag.")


class ModalOpenPlanner(OpenWeightsPlanner):
    """The open-weights cap planner, served from a Modal Endpoint."""

    name = "modal-open"
    partner = PROVIDER

    def __init__(self, client: Callable[..., dict], **kw) -> None:
        kw.setdefault("model", getattr(client, "model", DEFAULT_MODEL))
        super().__init__(client, **kw)


__all__ = ["ModalClient", "ModalError", "ModalOpenPlanner", "PROVIDER", "LEDGER_NAME",
           "DEFAULT_MODEL", "URL_ENV", "ID_ENV", "SECRET_ENV", "MODEL_ENV"]
