"""Voice guide for WattNot (Muse Voice Transcribe -> Spark Intent Classifier -> Read-only Action).

Classifies audio or text input into exactly one of three strictly read-only intents:
  (a) navigate: e.g. "compare runs" (opens compare with selection prompt) or
                "show last night's report" (resolves latest run by timestamp).
  (b) answer: read-only Q&A grounded exclusively in signed receipts (hash-verified)
              and EU efficiency report outputs, citing receipt_id or report_id.
              Refuses if query is not answerable from verified receipts.
  (c) configure: returns a pre-fill object for run setup (workload, stack, caps)
                 requiring user review before launching.

SAFETY ENFORCEMENT:
The guide must NEVER launch, stop, or change a run. Only actions in ALLOWED_ACTIONS
{"navigate", "answer", "prefill"} are permitted; any other action is rejected.
All answers are text-only (no TTS).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from wattnot.receipt.build import verify_hash
from wattnot.report.eu import fields_for

ALLOWED_ACTIONS = frozenset({"navigate", "answer", "prefill"})
ALLOWED_INTENTS = frozenset({"navigate", "answer", "configure"})


class VoiceGuideActionError(ValueError):
    """Raised when an action outside the read-only allowlist is requested."""


def transcribe_audio(
    audio_data: bytes | None,
    *,
    fallback_text: str = "",
    live: bool = False,
    mock: bool = True,
    ledger: Any = None,
) -> str:
    """Transcribe audio to text via Muse Voice Transcribe (or deterministic mock)."""
    if fallback_text and fallback_text.strip():
        return fallback_text.strip()

    is_mock = mock or (not live) or (os.environ.get("WATTNOT_META_MOCK") == "1")
    if not is_mock:
        # Live path: guarded client invocation for Muse Voice Transcribe
        from wattnot.meta.client import GuardedClient

        client = GuardedClient(model="muse-voice-transcribe", ledger=ledger)
        # Live audio transcription call
        resp = client.ask("Transcribe audio input", purpose="voice:transcribe")
        return str(resp.get("text", "")).strip()

    # Deterministic mock transcription from audio payload or default prompt
    if not audio_data:
        return "compare runs"

    # If audio bytes contain utf-8 plain text prefix for testing:
    try:
        decoded = audio_data.decode("utf-8")
        if decoded.isalnum() or " " in decoded:
            return decoded.strip()
    except UnicodeDecodeError:
        pass

    return "compare runs"


def classify_intent(text: str) -> str:
    """Spark deterministic classifier: categorise query into navigate, answer, or configure."""
    t = text.lower().strip()

    # Configure triggers
    if any(k in t for k in ("configure", "prefill", "set up", "setup", "prepare run", "parameters for")):
        return "configure"

    # Explicit question forms are answer intent (even if they mention 'last run' or 'latest')
    if any(t.startswith(q) for q in ("what", "how", "tell me", "is there", "which", "explain", "describe", "why")):
        return "answer"

    # Navigate triggers
    if any(k in t for k in ("compare", "compare runs", "show last night", "last night's report",
                            "show report", "latest report", "latest run", "last run", "open report", "navigate to")):
        return "navigate"

    # Default to answer (which will verify against signed receipts or refuse)
    return "answer"


def get_quick_ack(intent: str, text: str) -> dict[str, str]:
    """Return an instant (<300 ms) acknowledgment prior to full response streaming/resolution."""
    if intent == "navigate":
        if "compare" in text.lower():
            return {"intent": "navigate", "ack": "Opening Compare view...", "status": "ok"}
        return {"intent": "navigate", "ack": "Resolving latest report route...", "status": "ok"}
    if intent == "configure":
        return {"intent": "configure", "ack": "Preparing run setup pre-fill...", "status": "ok"}
    return {"intent": "answer", "ack": "Querying signed receipts and EU report...", "status": "ok"}


def _load_signed_receipts(root_dir: Path) -> list[dict[str, Any]]:
    """Scan and verify signed receipts from disk (receipts/ and verifier/samples/)."""
    verified: list[dict[str, Any]] = []
    candidates: list[Path] = []

    for d in (root_dir / "receipts", root_dir / "verifier" / "samples", root_dir / "web" / "dash"):
        if d.is_dir():
            candidates.extend(sorted(d.glob("*.json")))
            candidates.extend(sorted(d.glob("**/*.json")))

    for path in candidates:
        if path.name.endswith(".sigstore.json") or path.name.endswith(".schema.json"):
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict) or "integrity" not in doc or "receipt_id" not in doc:
                continue
            ok, _, _ = verify_hash(doc)
            if ok:
                verified.append(doc)
        except Exception:
            continue
    return verified


def process_voice_query(
    query_text: str | None = None,
    audio_data: bytes | None = None,
    *,
    root_dir: str | Path | None = None,
    live: bool = False,
    mock: bool = True,
    ledger: Any = None,
) -> dict[str, Any]:
    """Execute voice guide workflow: Audio/Text -> Spark intent -> Guarded Read-Only Action."""
    root = Path(root_dir) if root_dir else Path(__file__).resolve().parents[3]
    text = transcribe_audio(audio_data, fallback_text=query_text or "", live=live, mock=mock, ledger=ledger)

    intent = classify_intent(text)
    quick = get_quick_ack(intent, text)
    label = "MOCK" if (mock or not live) else "MEASURED"

    result: dict[str, Any]

    if intent == "navigate":
        t_low = text.lower()
        if "compare" in t_low:
            result = {
                "intent": "navigate",
                "action": "navigate",
                "route": "/dash/comparetree.html",
                "prompt": "Select 2 or more runs from the list to compare energy and latency Pareto fronts.",
                "text": "Navigating to Compare view. Please select runs to compare Pareto fronts.",
                "quick_ack": quick["ack"],
                "label": label,
            }
        else:
            # Resolve latest run by timestamp
            receipts = _load_signed_receipts(root)
            latest = None
            if receipts:
                receipts.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
                latest = receipts[0]
            latest_id = latest["receipt_id"] if latest else "rcpt_latest"
            result = {
                "intent": "navigate",
                "action": "navigate",
                "route": f"/dash/report.html?receipt={latest_id}",
                "target_receipt_id": latest_id,
                "text": f"Found latest run report {latest_id}. Navigating to report route.",
                "quick_ack": quick["ack"],
                "label": label,
            }

    elif intent == "configure":
        result = {
            "intent": "configure",
            "action": "prefill",
            "text": "Run setup parameters prepared. Please review workload, stack, and power caps, then press Start.",
            "prefill": {
                "workload": {
                    "name": "replay-v1",
                    "model": "meta-llama/Llama-3.1-8B-Instruct",
                    "n_requests": 200,
                },
                "stack": "vllm",
                "vllm_flags": {
                    "max_num_seqs": 256,
                    "gpu_memory_utilization": 0.90,
                },
                "caps_w": [300, 350, 400],
                "note": "Pre-filled by voice guide. Action required: review parameters and press Start.",
            },
            "quick_ack": quick["ack"],
            "label": label,
        }

    else:
        # Intent: answer. Grounded strictly in signed receipts and EU efficiency reports.
        receipts = _load_signed_receipts(root)
        t_low = text.lower()

        # Check if the query asks about energy, savings, water, or specific receipts
        answerable_keywords = ("saving", "savings", "energy", "wh", "kwh", "joule", "joules", "water", "cap", "receipt", "receipts", "grade")
        is_answerable = any(re.search(r"\b" + re.escape(k) + r"\b", t_low) for k in answerable_keywords) or any(
            bool(r.get("receipt_id")) and r["receipt_id"].lower() in t_low for r in receipts
        )

        if receipts and is_answerable:
            # Pick best matching receipt or latest
            matched = receipts[0]
            for r in receipts:
                if r.get("receipt_id", "").lower() in t_low:
                    matched = r
                    break

            rid = matched["receipt_id"]
            res = matched.get("result", {})
            saving = res.get("billed_saving_pct", res.get("saving_pct", {}).get("mean", 0.0))
            cand = matched.get("candidate", {})
            wh = cand.get("wh_per_req", {}).get("mean", 0.0)
            water = (matched.get("estimates") or {}).get("water_ml_per_req", {}).get("value", 0.0)

            # Generate EU efficiency report fields to cite
            eu_rows = fields_for(matched)
            kwh_row = next((row for row in eu_rows if row.get("field") == "it_energy_measured_window"), None)
            kwh_val = kwh_row.get("value") if kwh_row else 0.0

            ans_text = (
                f"According to signed receipt {rid} and EU report: "
                f"billed energy saving is {saving}% [MEASURED], "
                f"energy per request is {wh} Wh [MEASURED], "
                f"window IT energy is {kwh_val} kWh [MEASURED], "
                f"water estimate is {water} mL/req [ESTIMATE]."
            )

            result = {
                "intent": "answer",
                "action": "answer",
                "text": ans_text,
                "citations": [rid, f"report:eu:{rid}"],
                "quick_ack": quick["ack"],
                "label": label,
            }
        else:
            result = {
                "intent": "answer",
                "action": "answer",
                "text": (
                    f"I can only answer questions grounded in verified WattNot receipts or "
                    f"EU efficiency reports. No matching data was found for: '{text}'."
                ),
                "citations": [],
                "quick_ack": quick["ack"],
                "refusal": True,
                "label": label,
            }

    # Strict Safety Enforcement: Enforce allowlist of actions
    if result.get("action") not in ALLOWED_ACTIONS:
        raise VoiceGuideActionError(
            f"Action '{result.get('action')}' is not permitted. "
            f"Voice guide is strictly read-only and restricted to {sorted(ALLOWED_ACTIONS)}."
        )

    return result
