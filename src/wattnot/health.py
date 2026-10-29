"""Endpoint health check before a run, and the FALLBACK ledger.

Before a run we probe each endpoint the run may call: the Meta Model API (Muse Spark),
Baseten (Muse Spark Open) and Modal (Muse Spark Open). A probe is a free ``GET /models``
(no tokens, no spend). Each result goes into a ledger. When an endpoint is down, unset, or
does not list the model, the run uses the documented fallback and the ledger says so with a
``FALLBACK:`` line. The ledger is written into the receipt (``fallback_ledger``) and shown in
the UI. Keys are read from the environment and never logged.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime, timezone

Opener = Callable[[urllib.request.Request, float], tuple[int, bytes]]

ENDPOINTS = (
    {"id": "meta", "name": "Meta Model API (Muse Spark)", "base_env": "META_BASE_URL",
     "base": "https://api.meta.ai/v1", "key_env": "META_API_KEY", "auth": "Bearer",
     "model": "muse-spark", "fallback": "deterministic planner policy (bisect)"},
    {"id": "baseten", "name": "Baseten (Muse Spark Open)", "base_env": "BASETEN_BASE_URL",
     "base": "https://inference.baseten.co/v1", "key_env": "BASETEN_API_KEY", "auth": "Api-Key",
     "model": "muse-spark-open", "fallback": "Modal, else our own GPU only"},
    {"id": "modal", "name": "Modal (Muse Spark Open)", "base_env": "MODAL_ENDPOINT_URL",
     "base": None, "key_env": "MODAL_PROXY_TOKEN_ID", "auth": "modal-proxy",
     "model": "muse-spark-open", "fallback": "Baseten, else our own GPU only"},
)


def _urlopen(req: urllib.request.Request, timeout: float) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def _key(ep: Mapping, env: Mapping) -> str | None:
    if ep["id"] == "meta":
        if env.get("META_API_KEY"):
            return env["META_API_KEY"]
        try:
            from wattnot.meta.client import load_key
            return load_key() or None
        except Exception:
            return None
    if ep["id"] == "baseten":   # the one key lookup for Baseten (working key first, then the API key)
        from wattnot.cloud import inference_key
        return inference_key(env)[1] or None
    return env.get(ep["key_env"]) or None


def probe(ep: Mapping, *, env: Mapping | None = None, opener: Opener | None = None,
          timeout: float = 8.0) -> dict:
    env = os.environ if env is None else env
    base = env.get(ep["base_env"]) or ep["base"]
    row = {"id": ep["id"], "name": ep["name"], "checked_at":
           datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    key = _key(ep, env)
    if not base or not key:
        miss = "endpoint URL" if not base else "key"
        return {**row, "status": "unconfigured", "ok": False, "http": None, "latency_s": None,
                "why": f"no {miss} set", "fallback": f"FALLBACK: {ep['fallback']}"}
    headers = {}
    if ep["auth"] == "modal-proxy":
        headers = {"Modal-Key": key, "Modal-Secret": env.get("MODAL_PROXY_TOKEN_SECRET", "")}
    else:
        headers = {"Authorization": f"{ep['auth']} {key}"}
    req = urllib.request.Request(base.rstrip("/") + "/models", headers=headers)
    t0 = time.monotonic()
    try:
        status, raw = (opener or _urlopen)(req, timeout)
    except Exception as e:
        return {**row, "status": "down", "ok": False, "http": None,
                "latency_s": round(time.monotonic() - t0, 3), "why": type(e).__name__,
                "fallback": f"FALLBACK: {ep['fallback']}"}
    lat = round(time.monotonic() - t0, 3)
    if status != 200:
        why = {401: "key rejected", 403: "forbidden (billing or access)",
               404: "model_not_found or bad path"}.get(status, f"HTTP {status}")
        return {**row, "status": "down", "ok": False, "http": status, "latency_s": lat,
                "why": why, "fallback": f"FALLBACK: {ep['fallback']}"}
    listed = None
    try:
        ids = [m.get("id", "") for m in (json.loads(raw or b"{}").get("data") or [])]
        listed = any(ep["model"] in i.lower() for i in ids) if ids else None
    except (ValueError, AttributeError):
        pass
    if listed is False:
        return {**row, "status": "model_missing", "ok": False, "http": status, "latency_s": lat,
                "why": f"{ep['model']} not listed", "fallback": f"FALLBACK: {ep['fallback']}"}
    return {**row, "status": "ok", "ok": True, "http": status, "latency_s": lat, "why": "",
            "fallback": None}


def check_all(*, env: Mapping | None = None, opener: Opener | None = None,
              only: tuple[str, ...] | None = None) -> dict:
    """The ``fallback_ledger`` receipt block."""

    rows = [probe(ep, env=env, opener=opener) for ep in ENDPOINTS
            if only is None or ep["id"] in only]
    return {"spec": "wattnot.fallback-ledger/1", "label": "MEASURED",
            "entries": rows, "fallbacks": [r["fallback"] for r in rows if r["fallback"]],
            "all_ok": all(r["ok"] for r in rows)}


def log_fallback(ledger: dict, source: str, message: str) -> dict:
    """Append a run-time fallback (e.g. the planner fell back to bisect)."""

    ledger.setdefault("entries", []).append({"id": source, "status": "fallback", "ok": False,
                                             "why": message, "fallback": f"FALLBACK: {message}"})
    ledger.setdefault("fallbacks", []).append(f"FALLBACK: {message}")
    ledger["all_ok"] = False
    return ledger


def main(argv=None) -> int:  # pragma: no cover - thin CLI
    import argparse

    p = argparse.ArgumentParser(prog="wattnot health", description=__doc__)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    led = check_all()
    if a.json:
        print(json.dumps(led, indent=2))
    else:
        for r in led["entries"]:
            print(f"{'ok  ' if r['ok'] else 'DOWN'} {r['name']:<32} {r['status']:<13} "
                  f"{r['http'] or '':<4} {r['why']}  {r['fallback'] or ''}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
