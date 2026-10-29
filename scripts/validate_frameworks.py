#!/usr/bin/env python3
"""Validate the framework drop-ins for REAL against the local mock upstream.

Each example under ``examples/frameworks/<name>/agent.py`` is run end to end through the
WattNot proxy (``src/wattnot/agentwaste/server.py`` + ``frameworks.serve_mock``). The upstream
is a tiny in-process mock: no GPU, no network, no paid API. For every framework we record an
honest status:

* ``validated``  -> label ``VALIDATED-LOCAL (mock upstream)`` (the client really talked to the
  proxy and the proxy really drove the mock model).
* ``skipped``    -> label ``MOCK (not run)`` with the reason (library not installed).

The result is written to ``data/frameworks/validation.json`` (the static site reads it) and the
LangChain run's proxy-receipt is saved to ``receipts/frameworks/langchain-demo.json`` with its
``frameworks`` field populated.

Run one proxy at a time and shut it down before starting the next, so at most one local server
is up during validation. Nothing here reaches the public internet.

Regenerate with:  python scripts/validate_frameworks.py
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import urllib.request
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path

from wattnot.agentwaste.frameworks import serve_mock

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / "examples" / "frameworks"
ARTIFACT = ROOT / "data" / "frameworks" / "validation.json"
RECEIPT = ROOT / "receipts" / "frameworks" / "langchain-demo.json"

VALIDATED_LABEL = "VALIDATED-LOCAL (mock upstream)"
SKIPPED_LABEL = "MOCK (not run)"

# (example dir, framework name as the proxy reports it, import-probe module, pip dist name)
FRAMEWORKS = [
    ("openai_python", "openai-python", "openai", "openai"),
    ("langchain", "langchain", "langchain_openai", "langchain-openai"),
    ("llamaindex", "llamaindex", "llama_index.llms.openai_like", "llama-index-llms-openai-like"),
]


def _load_example(name: str):
    spec = importlib.util.spec_from_file_location(f"ex_{name}", EX / name / "agent.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dist_version(dist: str) -> str | None:
    try:
        return importlib_metadata.version(dist)
    except importlib_metadata.PackageNotFoundError:
        return None


def _receipt(base: str) -> dict:
    with urllib.request.urlopen(base + "/wattnot/receipt", timeout=5) as r:
        return json.loads(r.read())


def _probe(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def validate_one(ex_name: str, fw_name: str, probe: str, dist: str) -> tuple[dict, dict | None]:
    """Run one framework against a fresh mock proxy. Returns (entry, proxy_receipt|None)."""
    version = _dist_version(dist)
    if not _probe(probe):
        return ({
            "framework": fw_name,
            "status": "skipped",
            "label": SKIPPED_LABEL,
            "package": dist,
            "version": version,
            "reason": f"Library not installed: {probe} is not installed",
        }, None)

    base, server = serve_mock()
    try:
        mod = _load_example(ex_name)
        easy = mod.run(base, "Where do the signed receipts live?")
        hard = mod.run(base, "HARD: which module signs receipts?")
        receipt = _receipt(base)
    finally:
        server.should_exit = True

    easy_ok = "llama-3.1-8b" in (easy or "").lower()       # small model kept the easy one
    hard_escalated = "llama-3.1-8b" not in (hard or "").lower()  # hard one escalated past it
    entry = {
        "framework": fw_name,
        "status": "validated",
        "label": VALIDATED_LABEL,
        "package": dist,
        "version": version,
        "response": (easy or "").strip()[:160],
        "small_model_kept_easy": easy_ok,
        "hard_escalated": hard_escalated,
        "upstream_calls": receipt.get("agent_waste", {}).get("upstream_calls"),
        "receipt_frameworks": receipt.get("frameworks"),
        "receipt_label": receipt.get("label"),
    }
    return entry, receipt


def main() -> int:
    entries: list[dict] = []
    langchain_receipt: dict | None = None
    for ex_name, fw_name, probe, dist in FRAMEWORKS:
        entry, receipt = validate_one(ex_name, fw_name, probe, dist)
        entries.append(entry)
        status = entry["status"].upper()
        print(f"  {fw_name:<14} {status:<9} {entry['label']} (v{entry['version']})")
        if fw_name == "langchain" and receipt is not None:
            langchain_receipt = receipt

    validated = [e for e in entries if e["status"] == "validated"]
    artifact = {
        "spec": "wattnot.frameworks-validation/1",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "label": VALIDATED_LABEL,
        "upstream": "localhost mock upstream (no GPU, no network, no paid API)",
        "validated": len(validated),
        "total": len(entries),
        "frameworks": entries,
    }
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps(artifact, indent=2) + "\n")
    print(f"wrote {ARTIFACT.relative_to(ROOT)}  ({len(validated)}/{len(entries)} validated)")

    if langchain_receipt is not None:
        RECEIPT.parent.mkdir(parents=True, exist_ok=True)
        demo = {
            "spec": langchain_receipt.get("spec"),
            "source": "LangChain chat model → WattNot proxy → localhost mock upstream",
            "note": "Real drop-in run, mock upstream. Not a live model call.",
            **langchain_receipt,
        }
        RECEIPT.write_text(json.dumps(demo, indent=2) + "\n")
        print(f"wrote {RECEIPT.relative_to(ROOT)}  (frameworks={demo.get('frameworks')})")
    else:
        print("langchain not installed: demo receipt left unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
