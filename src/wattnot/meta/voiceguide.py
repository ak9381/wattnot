"""Voice guide for WattNot (Muse Voice Transcribe -> Spark Intent Classifier -> Read-only Action).

Classifies audio or text input into exactly one of four strictly read-only intents:
  (a) navigate: e.g. "compare runs" (opens compare with selection prompt) or
                "show last night's report" (resolves latest run by timestamp).
  (b) answer: read-only Q&A grounded exclusively in signed receipts (hash-verified)
              and EU efficiency report outputs, citing receipt_id or report_id.
              Refuses if query is not answerable from verified receipts.
  (c) configure: returns a read-only pre-fill object for run setup
                 (cap_w, workload, framework) requiring user review before launching.
                 Never starts, stops, or changes a run.
  (d) explain: point-and-explain for on-screen elements or metrics.
  (e) command: a spoken tuning command ("try 350W with FP8") planned by Spark and run
               through the normal guarded loop in dry-run (see wattnot.meta.tuner). It
               files proposals in a throwaway scratch store and returns a receipt summary;
               it never applies anything or touches hardware. The summary is read aloud by
               the browser's own speech synthesis (labelled "browser voice"), not a model.

SAFETY ENFORCEMENT:
The guide must NEVER launch, stop, or change a run. Only actions in ALLOWED_ACTIONS
{"navigate", "answer", "prefill", "explain", "tune"} are permitted; any other action is
rejected. The "tune" action is a dry-run plan only: it classifies and routes a change
through the guardrail but applies nothing. Answers are text-only on the backend; the
command summary is spoken only by the browser voice, never synthesised by a Meta model.
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

ALLOWED_ACTIONS = frozenset({"navigate", "answer", "prefill", "explain", "tune"})
ALLOWED_INTENTS = frozenset({"navigate", "answer", "configure", "explain", "command"})

# Canonical frameworks selectable via spoken run setup. Anything else -> None.
FRAMEWORKS = ("openai-python", "langchain", "llamaindex", "plain-api")

_FRAMEWORK_ALIASES: tuple[tuple[str, str], ...] = (
    ("openai-python", "openai-python"),
    ("openai python", "openai-python"),
    ("plain-api", "plain-api"),
    ("plain api", "plain-api"),
    ("plainapi", "plain-api"),
    ("llama-index", "llamaindex"),
    ("llama index", "llamaindex"),
    ("llamaindex", "llamaindex"),
    ("langchain", "langchain"),
    ("openai", "openai-python"),
)

_NUMBER_ONES: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_NUMBER_TENS: dict[str, int] = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_NUMBER_WORDS = frozenset([*_NUMBER_ONES, *_NUMBER_TENS, "hundred", "and"])

_KNOWN_WORKLOAD_IDS = frozenset({
    "replay-v1", "proof-v1", "prefix-cache-v1", "cloud-effort-v1", "muse-code-v1",
})

_WORKLOAD_STOPWORDS = frozenset({"the", "a", "an", "my", "that", "this", "that", "our"})


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
        # Live path: Muse Voice Transcribe file endpoint (POST /v1/asr/transcribe, multipart
        # 'request' JSON + 'audio' file). Verified live 2026-10-29, see receipts/run2/voice_transcribe_live.json.
        import urllib.request
        import uuid

        from wattnot.meta.client import DEFAULT_BASE, load_key

        bnd = uuid.uuid4().hex
        reqj = json.dumps({"model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV"})
        payload = (f'--{bnd}\r\nContent-Disposition: form-data; name="request"\r\n'
                   f'Content-Type: application/json\r\n\r\n{reqj}\r\n').encode()
        payload += (f'--{bnd}\r\nContent-Disposition: form-data; name="audio"; filename="a.wav"\r\n'
                    f'Content-Type: audio/wav\r\n\r\n').encode() + (audio_data or b"") + f"\r\n--{bnd}--\r\n".encode()
        req = urllib.request.Request(f"{DEFAULT_BASE}/asr/transcribe", data=payload, headers={
            "Authorization": f"Bearer {load_key()}", "Content-Type": f"multipart/form-data; boundary={bnd}"})
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
            data = json.loads(r.read() or b"{}")
        return str(data.get("transcript") or "").strip()

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

    # Real audio with no live transcriber: say we heard nothing rather than
    # invent a query (it used to answer "compare runs" for any recording).
    return ""


def _words_to_int(words: list[str]) -> int | None:
    """Parse English number words (e.g. ['three','hundred','fifty'] -> 350).

    Also handles colloquial shorthand '<one-nine> <tens>' like 'three fifty' -> 350.
    Returns None when the tokens do not form a number.
    """
    for w in words:
        if w not in _NUMBER_ONES and w not in _NUMBER_TENS and w not in ("hundred", "and"):
            return None
    tokens = [w for w in words if w != "and"]
    if not tokens:
        return None
    # Shorthand: single digit 1-9 followed directly by an exact ten ("three fifty" -> 350).
    if len(tokens) == 2 and tokens[0] in _NUMBER_ONES and tokens[1] in _NUMBER_TENS:
        ones = _NUMBER_ONES[tokens[0]]
        if 1 <= ones <= 9 and _NUMBER_TENS[tokens[1]] % 10 == 0:
            return ones * 100 + _NUMBER_TENS[tokens[1]]
    total = 0
    current = 0
    seen_digit = False
    for w in tokens:
        if w in _NUMBER_ONES:
            current += _NUMBER_ONES[w]
            seen_digit = True
        elif w in _NUMBER_TENS:
            current += _NUMBER_TENS[w]
            seen_digit = True
        elif w == "hundred":
            if not seen_digit:
                return None
            current *= 100
        else:
            return None
    total += current
    return total if seen_digit else None


def _parse_cap_w(text: str) -> int | None:
    """Extract a spoken power cap in watts. Handles '325 watts', '325W', '325 W' and number words."""
    t = text.lower().replace("-", " ")
    # Digit form: 325 watts / 325 watt / 325W / 325 W
    m = re.search(r"\b(\d{2,4})\s*(?:watts?|w)\b", t)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    # Number-word form: "<number words> watt(s)/w", e.g. "three fifty watts".
    tokens = re.findall(r"[a-z]+|\d+", t)
    for i, tok in enumerate(tokens):
        if tok in ("watt", "watts", "w") and i > 0:
            # Collect up to 5 preceding number-word tokens.
            start = i - 1
            while start >= 0 and (i - start) <= 5 and tokens[start] in _NUMBER_WORDS:
                start -= 1
            candidate = [w for w in tokens[start + 1:i] if w != "and"]
            if candidate and all(w in _NUMBER_WORDS for w in candidate):
                val = _words_to_int(candidate)
                if val is not None and 1 <= val <= 9999:
                    # A bare 'w' only counts when a number word precedes it.
                    return val
    return None


def _parse_workload(text: str) -> str | None:
    """Extract '<name> workload' or a known workload id. Returns None when unknown."""
    t = text.lower().replace("_", "-")
    m = re.search(r"([a-z0-9][a-z0-9\-]*)\s+workload", t)
    if m:
        name = m.group(1).strip("-")
        if name and name not in _WORKLOAD_STOPWORDS:
            return name
        return None
    for known in sorted(_KNOWN_WORKLOAD_IDS):
        if known in t:
            return known
    return None


def _parse_framework(text: str) -> str | None:
    """Map spoken framework mentions to canonical ids. Returns None when unknown."""
    t = text.lower().replace("_", " ").replace("-", " ")
    t_flat = re.sub(r"\s+", " ", t)
    for alias, canonical in _FRAMEWORK_ALIASES:
        norm = alias.replace("-", " ").replace("_", " ")
        if norm in t_flat or alias in text.lower():
            return canonical
    # Hyphenated ids may survive as-is in the raw text.
    raw = text.lower()
    for canonical in FRAMEWORKS:
        if canonical in raw:
            return canonical
    return None


def parse_run_setup(text: str) -> dict[str, Any]:
    """Parse spoken run setup into a pre-fill dict.

    Returns {"cap_w": int|None, "workload": str|None, "framework": str|None}.
    Unknown fields stay None. Never starts, stops, or changes a run.
    """
    return {
        "cap_w": _parse_cap_w(text),
        "workload": _parse_workload(text),
        "framework": _parse_framework(text),
    }


def _has_run_setup_signals(t: str) -> bool:
    """True when lowered text carries spoken run-setup evidence (cap, workload, or framework)."""
    if _parse_cap_w(t) is not None:
        return True
    if "workload" in t:
        return True
    lowered = t.lower()
    for alias, _ in _FRAMEWORK_ALIASES:
        if alias in lowered:
            return True
    for canonical in FRAMEWORKS:
        if canonical in lowered:
            return True
    return False


def classify_intent(text: str) -> str:
    """Spark deterministic classifier: navigate, answer, configure, explain, or command."""
    t = text.lower().strip()

    # A spoken tuning command ("try 350W with FP8") is planned by Spark and run through
    # the guarded loop (dry-run). It wins over configure/prefill when a knob is named, but
    # never steals a question ("what does FP8 mean?"): is_tune_command excludes those.
    from wattnot.meta.tuner import is_tune_command

    if is_tune_command(text):
        return "command"

    # Explicit configure keywords always win (except pure explain requests handled below).
    if any(k in t for k in ("configure", "prefill", "set up", "setup", "prepare run", "parameters for")):
        return "configure"

    # Explain triggers (point and explain on-screen elements or metrics).
    if any(k in t for k in ("explain", "what does this mean", "tell me about this number", "what is this number")) or (
        "what does" in t and "mean" in t
    ) or (
        t.startswith("what is") and any(ch.isdigit() or ch in "%$" for ch in t)
    ):
        return "explain"

    # Explicit question forms are answer intent (even if they mention 'last run' or 'latest').
    if any(t.startswith(q) for q in ("what", "how", "tell me", "is there", "which", "describe", "why")):
        return "answer"

    # Spoken run setup ("try 325 watts on the batch workload with langchain").
    if _has_run_setup_signals(t):
        return "configure"

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
    if intent == "command":
        return {"intent": "command", "ack": "Asking Spark to plan this (dry run)...", "status": "ok"}
    if intent == "configure":
        return {"intent": "configure", "ack": "Preparing run setup pre-fill...", "status": "ok"}
    if intent == "explain":
        return {"intent": "explain", "ack": "Locating element on page...", "status": "ok"}
    return {"intent": "answer", "ack": "Querying signed receipts and EU report...", "status": "ok"}


def _created_at_key(receipt: Mapping) -> tuple[int, Any]:
    """Timezone-aware sort key for created_at (lexicographic ISO strings mis-order offsets)."""
    from datetime import datetime, timezone

    raw = str(receipt.get("created_at", ""))
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (1, dt)
    except ValueError:
        return (0, raw)


def _load_signed_receipts(root_dir: Path) -> list[dict[str, Any]]:
    """Scan receipts/ and keep only verified run receipts.

    A receipt qualifies when its JCS sha256 recomputes (hash check) AND, when an
    offline signature bundle sits next to it, the Ed25519 signature verifies and
    the signer is in verifier/trusted-keys.json (when that file exists). Verifier
    forgery fixtures and UI sample JSON are not grounding data.
    """
    from wattnot.receipt.sign import bundle_path, verify_offline_bundle

    verified: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    receipts_dir = root_dir / "receipts"
    if not receipts_dir.is_dir():
        return []

    trusted: set[str] | None = None
    tk = root_dir / "verifier" / "trusted-keys.json"
    if tk.is_file():
        try:
            trusted = {k["signer"] for k in json.loads(tk.read_text(encoding="utf-8"))["keys"]}
        except Exception:
            trusted = None

    for path in sorted(receipts_dir.rglob("*.json")):
        if path.name.endswith(".sigstore.json") or path.name.endswith(".schema.json"):
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict) or "integrity" not in doc or "receipt_id" not in doc:
                continue
            ok, _, _ = verify_hash(doc)
            if not ok:
                continue
            bp = bundle_path(path)
            if bp.is_file():
                bundle = json.loads(bp.read_text(encoding="utf-8"))
                sig_ok, signer = verify_offline_bundle(doc, bundle)
                if not sig_ok:
                    continue
                named = (doc.get("integrity") or {}).get("signer")
                if named not in (None, signer):
                    continue  # bundle self-verifies but under a different key than claimed
                if trusted is not None and signer not in trusted:
                    continue
            rid = str(doc["receipt_id"])
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            verified.append(doc)
        except Exception:
            continue
    return verified


def _p99_limit_pct() -> str:
    """The p99 gate's allowed slack, read from the gate code so the limit is not
    restated as a literal here (default 10%)."""
    import inspect

    from wattnot.gates.latency import p99_gate

    slack = inspect.signature(p99_gate).parameters["slack"].default
    return f"{float(slack) * 100:.0f}%"


def _wonder_trials(receipts: list[dict[str, Any]], receipt_id: str) -> list[dict[str, Any]]:
    """Return the Wonder addendum's gate trials for a receipt, or [] when absent."""
    want = f"{receipt_id}.wonder_live".lower()
    for r in receipts:
        if str(r.get("receipt_id", "")).lower() == want:
            trials = (r.get("data") or {}).get("trials")
            if isinstance(trials, list):
                return trials
    return []


def _cap_gate_answer(matched: Mapping, receipts: list[dict[str, Any]], cap_w: int) -> str | None:
    """Answer "why was <N> W rejected" from the signed Wonder addendum trials.

    Uses the same measured facts the static page shows (p99 latency rise vs the
    gate limit, joules per correct answer, fixed-check pass rate) with the
    trial's own label. Returns None when that cap's trial is not in the receipt,
    so the caller keeps the honest refusal.
    """
    rid = str(matched.get("receipt_id", ""))
    trial = next((t for t in _wonder_trials(receipts, rid) if t.get("cap_w") == cap_w), None)
    if trial is None:
        return None
    p99, jpc, quality = trial.get("p99_delta"), trial.get("joules_per_correct"), trial.get("quality")
    if p99 is None or jpc is None or quality is None:
        return None
    label = str(trial.get("label", "MEASURED"))
    limit = _p99_limit_pct()
    if trial.get("accepted_by_gates"):
        verdict = f"kept: p99 latency rose {p99 * 100:.1f}%, within the {limit} limit"
    else:
        verdict = f"rejected: p99 latency rose {p99 * 100:.1f}% against the {limit} limit"
    return (
        f"According to signed receipt {rid} (Wonder addendum trials): {cap_w} W was "
        f"{verdict}. Joules per correct answer {jpc} J, fixed-check pass rate "
        f"{quality * 100:.1f}% [{label}]."
    )


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
    if audio_data and not (query_text or "").strip() and not text:
        return {
            "intent": "answer",
            "action": "none",
            "transcript": "",
            "text": ("Voice transcription is off on this server, so the recording was not turned into text. "
                     "Type your question instead." if (mock or not live) else
                     "Muse Voice Transcribe returned no words for that recording. Try again or type your question."),
            "label": "MOCK" if (mock or not live) else "LIVE",
        }

    intent = classify_intent(text)
    quick = get_quick_ack(intent, text)
    # Top-level label describes the voice path; numbers carry their own receipt labels.
    label = "MOCK" if (mock or not live) else "LIVE"

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
                receipts.sort(key=_created_at_key, reverse=True)
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

    elif intent == "command":
        # Spoken tuning command: Spark plans it and the guarded loop routes it in
        # dry-run (nothing is applied, no hardware touched). The receipt summary is
        # read aloud by the browser voice on the page.
        from wattnot.meta.tuner import run_tune

        result = run_tune(text)
        result.setdefault("quick_ack", quick["ack"])

    elif intent == "configure":
        # Read-only pre-fill only: parse spoken setup, never start/stop/change a run.
        parsed = parse_run_setup(text)
        cap_w = parsed["cap_w"]
        framework = parsed["framework"]
        result = {
            "intent": "configure",
            "action": "prefill",
            "text": "Run setup parameters prepared. Please review workload, stack, and power caps, then press Start.",
            "prefill": {
                "cap_w": cap_w,
                "workload": parsed["workload"],
                "framework": framework,
                "stack": framework if framework is not None else "vllm",
                "vllm_flags": {
                    "max_num_seqs": 256,
                    "gpu_memory_utilization": 0.90,
                },
                "caps_w": [cap_w] if cap_w is not None else [300, 350, 400],
                "note": (
                    "Pre-filled by voice guide. Action required: review parameters and press Start. "
                    "This pre-fill never starts, stops, or changes a run."
                ),
            },
            "quick_ack": quick["ack"],
            "label": label,
        }

    elif intent == "explain":
        result = {
            "intent": "explain",
            "action": "explain",
            "target_query": text,
            "text": f"Found element matching '{text}' on current page. Highlighting element and showing explanation.",
            "quick_ack": quick["ack"],
            "label": label,
        }

    else:
        # Intent: answer. Grounded strictly in signed receipts and EU efficiency reports.
        receipts = _load_signed_receipts(root)
        t_low = text.lower()
        # "run two" / "run to" (a common mishearing) / "run 2" -> "run2", so receipts match by run.
        t_low = re.sub(r"\brun[\s_-]*(?:two|to|too|2)\b", "run2", t_low)
        t_low = re.sub(r"\brun[\s_-]*(?:one|1)\b", "run1", t_low)
        run_tok = re.search(r"\brun\d+\b", t_low)

        # Pick the best matching receipt (by id, then by run tag), else the latest.
        matched: dict[str, Any] | None = None
        if receipts:
            receipts.sort(key=_created_at_key, reverse=True)
            matched = receipts[0]
            for r in receipts:
                if r.get("receipt_id", "").lower() in t_low:
                    matched = r
                    break
            else:
                if run_tok:
                    tag = f"_{run_tok.group(0)}_"
                    for r in receipts:
                        if tag in r.get("receipt_id", "").lower() + "_":
                            matched = r
                            break

        # "why was 325 watts rejected" (and similar "why was <N> W rejected") is
        # answered from the Wonder addendum's gate trials instead of a refusal.
        cap_w = _parse_cap_w(text)
        asks_gate = any(k in t_low for k in ("reject", "fail", "why not", "blocked", "declin"))
        gate_text = (
            _cap_gate_answer(matched, receipts, cap_w)
            if (matched is not None and cap_w is not None and asks_gate)
            else None
        )

        # Check if the query asks about energy, savings, water, or specific receipts
        answerable_keywords = ("saving", "savings", "energy", "wh", "kwh", "joule", "joules", "water", "cap", "receipt", "receipts", "grade",
                               "save", "saved", "saves")
        is_answerable = any(re.search(r"\b" + re.escape(k) + r"\b", t_low) for k in answerable_keywords) or any(
            bool(r.get("receipt_id")) and r["receipt_id"].lower() in t_low for r in receipts
        )

        if gate_text is not None:
            result = {
                "intent": "answer",
                "action": "answer",
                "text": gate_text,
                "citations": [matched["receipt_id"], f"{matched['receipt_id']}.wonder_live"],
                "quick_ack": quick["ack"],
                "label": label,
            }
        elif matched is not None and is_answerable:
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

            # Labels come from the receipt itself, never hardcoded: the measurement
            # block labels the energy numbers, the EU row labels the kWh figure.
            meas_label = str((matched.get("measurement") or {}).get("label", "SIMULATED"))
            kwh_label = str(kwh_row.get("label", meas_label)) if kwh_row else meas_label
            water_label = str(((matched.get("estimates") or {}).get("water_ml_per_req") or {})
                              .get("label", "ESTIMATE"))

            ans_text = (
                f"According to signed receipt {rid} and EU report: "
                f"billed energy saving is {saving}% [{meas_label}], "
                f"energy per request is {wh} Wh [{meas_label}], "
                f"window IT energy is {kwh_val} kWh [{kwh_label}], "
                f"water estimate is {water} mL/req [{water_label}]."
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

    if audio_data and not (query_text or "").strip():
        result.setdefault("transcript", text)
    return result
