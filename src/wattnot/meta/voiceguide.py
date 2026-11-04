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
from wattnot.report.eu import check_receipt_hash, fields_for


def _hash_ok(receipt) -> bool:
    """Never cite a receipt whose SHA-256 does not match (fail closed)."""
    try:
        check_receipt_hash(receipt)
        return True
    except ValueError:
        return False

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
    live: bool = True,
    mock: bool | None = None,
    ledger: Any = None,
) -> str:
    """Transcribe audio to text via Muse Voice Transcribe (or deterministic mock)."""
    if fallback_text and fallback_text.strip():
        return fallback_text.strip()

    from wattnot.meta.client import meta_mock_on, require_key

    is_mock = bool(mock) or meta_mock_on() or not live
    if not is_mock:
        require_key()
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
    t = re.sub(r"\bgsm[\s-]*8\s*k\b", "gsm8k", t)  # 'GSM 8k' as transcribed
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


_ON_WORDS = ("on", "enabled", "enable", "yes", "true")
_OFF_WORDS = ("off", "disabled", "disable", "no", "none", "false", "without")


def _parse_toggle(text: str, names: tuple[str, ...]) -> bool | None:
    """'thinking budget on' / 'turn off prefix caching' / 'without FP8' -> True/False; None if not named.
    Each setting is read inside its own clause, so 'thinking off, prefix caching on' keeps both."""
    for clause in re.split(r"[,;.]|\band\b|\bwith\b(?=\s+\w+\s+\w+\s+(?:on|off))", text.lower()):
        c = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", clause)).strip()
        for name in names:
            if name in c:
                words = c.replace(name, " ").split()
                return not any(w in _OFF_WORDS for w in words)
    return None


_PROVIDERS = (("baseten", "baseten"), ("base ten", "baseten"), ("base den", "baseten"), ("modal", "modal_a100"), ("a100", "modal_a100"),
              ("h100", "own_gpu"), ("own gpu", "own_gpu"), ("my gpu", "own_gpu"), ("replay", "replay"))
_MODELS = (("glimmer", "meta-models/Muse-Glimmer-30B"), ("spark open", "muse-spark-open"),
           ("spark", "muse-spark-1.3"))


def _parse_number_after(text: str, pattern: str) -> float | None:
    m = re.search(pattern, text.lower())
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


def parse_run_setup(text: str) -> dict[str, Any]:
    """Parse spoken run setup into a pre-fill dict.

    Keys: cap_w, workload, framework, thinking_budget, prefix_cache, fp8, provider, model,
    p99_pct, rounds, max_spend_usd. Unknown fields stay None. Never starts, stops, or changes a run.
    """
    t = text.lower()
    provider = next((v for k, v in _PROVIDERS if k in t), None)
    model = next((v for k, v in _MODELS if k in t), None)
    p99 = _parse_number_after(t, r"(?:p ?99|latency)[^0-9]{0,20}(\d{1,2})\s*(?:%|percent)")
    rounds = _parse_number_after(t, r"(\d)\s*rounds?\b")
    if rounds is None:
        m = re.search(r"\b(one|two|three)\s+rounds?\b", t)
        rounds = {"one": 1, "two": 2, "three": 3}[m.group(1)] if m else None
    spend = _parse_number_after(t, r"(?:\$|spend(?:ing)?\D{0,12}\$?)\s*(\d+(?:\.\d+)?)")
    return {
        "cap_w": _parse_cap_w(text),
        "workload": _parse_workload(text),
        "framework": _parse_framework(text),
        "thinking_budget": _parse_toggle(text, ("thinking budget", "thinking")),
        "prefix_cache": _parse_toggle(text, ("prefix caching", "prefix cache")),
        "fp8": _parse_toggle(text, ("fp8", "fp 8")),
        "provider": provider,
        "model": model,
        "p99_pct": int(p99) if p99 is not None else None,
        "rounds": int(rounds) if rounds is not None else None,
        "max_spend_usd": spend,
    }


_SETTING_WORDS = {"cap_w": "power cap {} W", "workload": "workload {}", "framework": "framework {}",
                  "provider": "platform {}", "model": "model {}", "p99_pct": "p99 limit {}%",
                  "rounds": "{} round(s)", "max_spend_usd": "spend cap ${}"}


def describe_setup(parsed: Mapping[str, Any]) -> str:
    bits = []
    for k, v in parsed.items():
        if v is None:
            continue
        if isinstance(v, bool):
            bits.append(f"{k.replace('_', ' ').replace('fp8', 'FP8')} {'on' if v else 'off'}")
        else:
            v = f"{v:g}" if isinstance(v, float) else v
            bits.append(_SETTING_WORDS.get(k, k + " {}").format(v))
    return ", ".join(bits)


def explicit_intent(text: str) -> str | None:
    """Fast path: an explicit 'configure/set up' or 'explain the page' request needs no Spark call."""
    t = text.lower().strip()
    if any(k in t for k in ("configure", "set up a run", "setup a run", "set up run", "prefill", "prepare a run")):
        return "configure"
    if re.match(r"^(please )?explain\b", t) or re.search(r"explain (the|this|my) (dashboard|page|screen|view)", t):
        return "explain"
    return None


def is_page_explain(text: str) -> bool:
    t = text.lower()
    return bool(re.search(r"(dashboard|page|screen|view|everything|what am i looking at|what's on)", t))


_NUM_RE = re.compile(r"[-+−]?\$?\d[\d,]*(?:\.\d+)?(?:/\d[\d,]*)?\s*(?:%|W|J|s|ms|Wh|kJ|pts?)?")


def summarize_screen(cards: Any, limit: int = 4) -> str:
    """A short spoken summary of what is on screen, built only from the card text the page sent."""
    items = []
    for c in cards if isinstance(cards, list) else []:
        if not isinstance(c, Mapping):
            continue
        title = str(c.get("title") or "").strip()[:60]
        body = re.sub(r"\s+", " ", str(c.get("text") or "")).strip()
        if not title and not body:
            continue
        nums = [n.strip() for n in _NUM_RE.findall(body) if any(ch.isdigit() for ch in n)][:2]
        label = next((w for w in ("MEASURED", "ESTIMATE", "SIMULATED", "MOCK", "REPLAY") if w in body), "")
        items.append((title or body[:40], nums, label))
    if not items:
        return ""
    parts = []
    for title, nums, label in items[:limit]:
        p = title
        if nums:
            p += ": " + " and ".join(nums)
        if label:
            p += f" ({label.lower()})"
        parts.append(p)
    more = f" Plus {len(items) - limit} more cards." if len(items) > limit else ""
    return (f"This page has {len(items)} card{'s' if len(items) != 1 else ''}. " + ". ".join(parts) + "." + more +
            " Lower energy per correct answer with the pass rate held is the win; MEASURED numbers come from the GPU meter.")


_SAVER_WORDS = {"agent_waste": ("agent waste", "waste guard", "loop guard"), "routing": ("routing", "tiers", "step up"),
                "context_saver": ("context saver", "context"), "thinking_cap": ("thinking cap", "thinking budget", "thinking"),
                "vision": ("vision", "image"), "voice": ("voice", "speech", "audio")}


def parse_deploy(text: str) -> dict[str, Any]:
    """Spoken deployment setup -> fields of the Plug-in screen. Unknown stays None. Read-only."""
    t = text.lower()
    out: dict[str, Any] = {"deployment": None, "upstream": None, "savers_off": [], "cap_min_w": None,
                           "cap_max_w": None, "sam_rack_check": None, "langfuse": None, "port": None}
    if "both" in t or ("gateway" in t and ("gpu" in t or "node" in t)):
        out["deployment"] = "both"
    elif "gateway" in t or "base url" in t or "proxy" in t:
        out["deployment"] = "gateway"
    elif "gpu controller" in t or "node agent" in t or "power cap" in t:
        out["deployment"] = "gpu"
    for k, words in (("meta", ("meta api", "muse spark")), ("vllm", ("vllm", "v llm")), ("baseten", ("baseten", "base ten"))):
        if any(w in t for w in words):
            out["upstream"] = k
    for key, words in _SAVER_WORDS.items():
        for w in words:
            if re.search(rf"(?:no|without|disable|turn off|switch off)\s+(?:the\s+)?{w}|{w}\s+off\b", t):
                out["savers_off"].append(key)
                break
    m = re.search(r"(\d{3})\s*(?:to|-|and)\s*(\d{3})\s*(?:w|watt)", t)
    if m:
        lo, hi = sorted((int(m.group(1)), int(m.group(2))))
        out["cap_min_w"], out["cap_max_w"] = lo, hi
    if re.search(r"(?:no|without|skip|disable)\s+(?:the\s+)?(?:sam|rack check)", t):
        out["sam_rack_check"] = False
    elif "rack check" in t or "sam" in t.split():
        out["sam_rack_check"] = True
    if "langfuse" in t:
        out["langfuse"] = not re.search(r"(?:no|without|disable)\s+langfuse", t)
    m = re.search(r"port\s+(\d{4,5})", t)
    if m:
        out["port"] = int(m.group(1))
    return out


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


INTENTS = ("navigate", "answer", "configure", "explain", "command")


def spark_classify_intent(text: str, *, ledger: Any = None) -> str:
    """Live Muse Spark intent classifier. Raises on any failure so the caller can say so."""
    from wattnot.meta.client import DEFAULT_BASE, GuardedClient, load_key

    key = load_key()
    if not key:
        raise RuntimeError("META_API_KEY is not set")
    body = {"model": os.environ.get("WATTNOT_SPARK_MODEL", "muse-spark-1.3"),
            "reasoning_effort": "minimal", "max_tokens": 600,
            "messages": [{"role": "system", "content": (
                "Classify a WattNot dashboard voice request. navigate = open a view or report; "
                "answer = a question about results; configure = set up a run; explain = what a "
                "number or element on screen means; command = a tuning command like 'try 350W with FP8'.")},
                {"role": "user", "content": text[:500]}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "Intent", "strict": True,
                "schema": {"type": "object", "additionalProperties": False, "required": ["intent"],
                           "properties": {"intent": {"type": "string", "enum": list(INTENTS)}}}}}}
    data = GuardedClient(key=key, base=os.environ.get("META_BASE_URL", DEFAULT_BASE),
                         ledger=ledger, timeout_s=30)(body, purpose="voiceguide_intent")
    intent = json.loads(data["choices"][0]["message"]["content"])["intent"]
    if intent not in INTENTS:
        raise ValueError("bad intent")
    return intent


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
    """The p99 gate's allowed slack, read from the one user setting (wattnot.settings) so the limit is not
    restated as a literal here (default 10%)."""
    from wattnot.settings import max_p99_increase

    return f"{max_p99_increase() * 100:.0f}%"


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
    rr = matched.get("rerun") if isinstance(matched.get("rerun"), Mapping) else None
    sw = next((x for x in (rr or {}).get("sweep") or [] if x.get("cap_w") == cap_w), None)
    if sw is not None:
        lim = f"{float(rr.get('p99_limit_pct', 10)):g}%"
        rise = sw["p99_change_pct"]
        verdict = (f"kept: p99 latency rose {rise:.1f}%, within the {lim} limit" if sw.get("accepted")
                   else f"rejected: p99 latency rose {rise:.1f}% against the {lim} limit")
        return (f"According to signed receipt {rid} (H100 rerun sweep): {cap_w} W was {verdict}. "
                f"Energy per request {sw['energy_per_req_change_pct']:+.1f}% vs stock, pass rate "
                f"{sw['pass_pct']['cap']:.1f}% [{sw.get('label', 'MEASURED')}].")
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
    live: bool = True,
    mock: bool | None = None,
    ledger: Any = None,
    spark_live: bool | None = None,
    screen: Any = None,
) -> dict[str, Any]:
    """Execute voice guide workflow: Audio/Text -> Spark intent -> Guarded Read-Only Action.

    Live by default. Keyword intent is the labelled MOCK, used only with WATTNOT_META_MOCK=1
    (or mock=True in tests). With no key it returns a clear error, never a guessed answer."""
    from wattnot.meta.client import load_key, meta_mock_on

    if mock is None:
        mock = meta_mock_on()
    if spark_live is None:
        spark_live = not mock
    if not mock and not load_key():
        return {"intent": "answer", "action": "none", "label": "ERROR", "code": "key_missing",
                "text": "Live Ask needs META_API_KEY (or run `muse login`). No answer was made up."}
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

    intent_source = "keywords (MOCK)"
    fast = explicit_intent(text)
    if fast:
        intent, intent_source = fast, "explicit request (no model call)"
    elif spark_live:
        try:
            intent = spark_classify_intent(text, ledger=ledger)
            intent_source = "Muse Spark (LIVE)"
        except Exception as exc:  # noqa: BLE001 - say it failed, never pretend
            return {"intent": "answer", "action": "none", "label": "ERROR", "code": "spark_failed",
                    "transcript": text,
                    "text": f"Muse Spark could not read that request ({type(exc).__name__}). Try again."}
    else:
        intent = classify_intent(text)
    quick = get_quick_ack(intent, text)
    # Top-level label describes the voice path; numbers carry their own receipt labels.
    label = "MOCK" if (mock or not live) else "LIVE"
    if fast and not mock and live and audio_data:
        label = "LIVE"
    if spark_live and intent_source.startswith("Muse Spark"):
        label = "LIVE"

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
        said = describe_setup(parsed)
        result = {
            "intent": "configure",
            "action": "prefill",
            "route": "/dash/v2.html#pCfg",
            "starts_run": False,
            "text": (f"I filled the Set up a run form with: {said}. Check it and press Start yourself."
                     if said else "I opened Set up a run, but heard no settings. Say a cap, e.g. 375 watts, thinking budget on."),
            "prefill": {
                **parsed,
                "framework": framework,
                "stack": framework if framework is not None else "vllm",
                "vllm_flags": {
                    "max_num_seqs": 256,
                    "gpu_memory_utilization": 0.90,
                },
                "caps_w": [cap_w] if cap_w is not None else [300, 350, 400],
                "deploy": parse_deploy(text),
                "note": (
                    "Pre-filled by voice guide. Action required: review parameters and press Start. "
                    "This pre-fill never starts, stops, or changes a run."
                ),
            },
            "quick_ack": quick["ack"],
            "label": label,
        }

    elif intent == "explain":
        page = is_page_explain(text)
        summary = summarize_screen(screen) if page else ""
        result = {
            "intent": "explain",
            "action": "explain",
            "scope": "page" if page else "element",
            "target_query": text,
            "speak": summary or None,
            "text": summary or (
                "I could not read the cards on this page. Hover a card and tap Ask for its explanation."
                if page else f"Explaining '{text}' on this page."),
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
                "citations": ([matched["receipt_id"]] if matched.get("rerun")
                              else [matched["receipt_id"], f"{matched['receipt_id']}.wonder_live"]),
                "quick_ack": quick["ack"],
                "label": label,
            }
        elif matched is not None and is_answerable and _hash_ok(matched):
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
            rr = matched.get("rerun") if isinstance(matched.get("rerun"), Mapping) else None
            if rr:
                lim = rr.get("p99_limit_pct")
                parts = [f"Rerun on {matched.get('subject', {}).get('model', 'the served model')}"
                         f" (p99 limit +{lim:g}%) [{rr.get('label', 'MEASURED')}]:"]
                for sw in rr.get("sweep") or []:
                    verdict = "kept" if sw.get("accepted") else "rejected: " + "; ".join(sw.get("why_not") or [])
                    parts.append(f"{sw['cap_w']} W {sw['energy_per_req_change_pct']:+.1f}% energy/request, "
                                 f"p99 {sw['p99_change_pct']:+.1f}% ({verdict});")
                cb = rr.get("combined_cap_plus_prefix") or {}
                if cb:
                    parts.append(f"{cb['cap_w']} W + prefix caching {cb['j_per_correct_change_pct']:+.1f}% "
                                 f"energy per correct answer vs stock;")
                for name, pb in (rr.get("public_benchmarks") or {}).items():
                    sig = "significant" if pb.get("ci95_excludes_zero") else "inconclusive"
                    parts.append(f"{name} {pb['j_per_correct_change_pct']:+.0f}% J/correct "
                                 f"(p={pb['p_value']:.2g}, {sig});")
                ans_text += " " + " ".join(parts).rstrip(";") + "."

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
    if isinstance(result, dict):
        result.setdefault("intent_source", intent_source)
    return result
