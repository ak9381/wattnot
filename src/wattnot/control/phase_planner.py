"""Muse Spark picks the phase-clock thresholds; our code keeps them safe.

The phase-aware sidecar (``wattnot.control.phase_clock``) needs three numbers: the prefill/decode
ratio that splits a scheduler window into prefill-heavy or decode-heavy, and the two SM clocks to
lock for each phase (high for compute-bound prefill, low for memory-bound decode). This module
asks Muse Spark for those numbers from a tiny summary, then:

* validates the answer against a fixed schema,
* clamps every value into the driver's safe range and forces ``low <= high``,
* falls back to the deterministic sweep if the call fails, times out or returns something unusable.

Every attempt writes one trace record: the SHA-256 of the exact prompt, the choice, the call
latency, the dollar cost (tokens x list price, an ESTIMATE) and, on a fallback, the reason. The
trace never carries prompts or model outputs, only numbers and ids.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass

MODEL = "muse-spark-1.3"
# H100 SM clock bounds (MHz) and the prefill/decode ratio bounds we will accept, then clamp into.
CLOCK_MIN, CLOCK_MAX = 210, 2100
RATIO_MIN, RATIO_MAX = 1.0, 16.0
MAX_REASON = 200

THRESHOLD_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["prefill_ratio", "high_mhz", "low_mhz", "reason"],
    "properties": {
        "prefill_ratio": {"type": "number"},
        "high_mhz": {"type": "integer"},
        "low_mhz": {"type": "integer"},
        "reason": {"type": "string", "maxLength": MAX_REASON},
    },
}

SYSTEM = (
    "You set the phase-aware GPU clock thresholds for an LLM server. Return JSON: prefill_ratio "
    "(a window is prefill-heavy when prompt tokens exceed this times generated tokens), high_mhz "
    "(SM clock to lock during prefill), low_mhz (SM clock during decode). Lower decode clocks save "
    "energy; keep p99 inside budget. One short reason quoting a number. JSON only."
)


@dataclass(frozen=True)
class PhaseThresholds:
    prefill_ratio: float
    high_mhz: int
    low_mhz: int
    source: str = "spark"  # spark | sweep
    clamped: bool = False
    reason: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def summarize(*, high_cap_mhz: int, sweep_lows: list[int], base_j_per_req: float,
              p99_budget_ms: int, decode_share_pct: float) -> dict:
    """The only thing Muse Spark sees. Numbers only (about 60 tokens)."""

    return {"clock_range_mhz": [CLOCK_MIN, CLOCK_MAX], "ratio_range": [RATIO_MIN, RATIO_MAX],
            "high_cap_mhz": high_cap_mhz, "sweep_low_candidates_mhz": list(sweep_lows),
            "base_j_per_req": round(base_j_per_req, 5), "p99_budget_ms": int(p99_budget_ms),
            "decode_share_pct": round(decode_share_pct, 1)}


def clamp(prefill_ratio: float, high_mhz: int, low_mhz: int) -> tuple[float, int, int, bool]:
    """Clamp into the safe ranges and force low <= high. Returns (ratio, high, low, clamped)."""

    r = max(RATIO_MIN, min(RATIO_MAX, float(prefill_ratio)))
    hi = max(CLOCK_MIN, min(CLOCK_MAX, int(high_mhz)))
    lo = max(CLOCK_MIN, min(CLOCK_MAX, int(low_mhz)))
    if lo > hi:
        lo = hi
    changed = (r != float(prefill_ratio)) or (hi != int(high_mhz)) or (lo != int(low_mhz))
    return r, hi, lo, changed


def sweep_fallback(sweep_lows: list[int], high_cap_mhz: int, reason: str) -> PhaseThresholds:
    """The deterministic fallback: use the sweep's lowest candidate decode clock, ratio 4.0."""

    low = min(sweep_lows) if sweep_lows else CLOCK_MIN
    r, hi, lo, _ = clamp(4.0, high_cap_mhz, low)
    return PhaseThresholds(r, hi, lo, source="sweep", reason=reason)


def request_body(summary: Mapping, *, model: str = MODEL, max_tokens: int = 200) -> dict:
    return {"model": model, "reasoning_effort": "minimal", "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": json.dumps(summary, separators=(",", ":"))}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "PhaseThresholds", "schema": THRESHOLD_SCHEMA, "strict": True}}}


def _prompt_sha256(body: Mapping) -> str:
    user = next((m["content"] for m in body.get("messages", []) if m.get("role") == "user"), "")
    return hashlib.sha256(user.encode()).hexdigest()


def _usd(model: str, tokens: Mapping) -> float | None:
    try:
        from wattnot.meta.ledger import price_for
        p, _ = price_for(model)
        return round(tokens.get("input", 0) * p.input_per_m / 1e6
                     + tokens.get("output", 0) * p.output_per_m / 1e6, 6)
    except Exception:
        return None


def _parse(data: Mapping) -> tuple[float, int, int, str] | None:
    try:
        r = float(data["prefill_ratio"])
        hi = int(data["high_mhz"])
        lo = int(data["low_mhz"])
    except (KeyError, TypeError, ValueError):
        return None
    reason = " ".join(str(data.get("reason", "")).split())[:MAX_REASON]
    if not reason:
        return None
    return r, hi, lo, reason


def choose(summary: Mapping, *, transport: Callable[[dict], dict] | None, model: str = MODEL,
           on_trace: Callable[[dict], None] | None = None,
           clock: Callable[[], float] = time.perf_counter) -> PhaseThresholds:
    """Ask Spark for thresholds, validate and clamp; fall back to the sweep on any problem.

    ``transport`` makes the chat-completions call and returns the raw response; pass ``None`` to
    skip the model entirely and take the sweep. ``on_trace`` receives one trace record per call.
    """

    body = request_body(summary, model=model)
    sha = _prompt_sha256(body)
    sweep_lows = list(summary.get("sweep_low_candidates_mhz") or [])
    high_cap = int(summary.get("high_cap_mhz", CLOCK_MAX))
    latency_s: float | None = None
    usd: float | None = None
    fallback_reason: str | None = None

    if transport is None:
        chosen = sweep_fallback(sweep_lows, high_cap, "no planner transport: kept the sweep")
        fallback_reason = chosen.reason
    else:
        t0 = clock()
        raw: Mapping | None = None
        try:
            raw = transport(body)
        except Exception as exc:  # any failure -> deterministic sweep
            fallback_reason = f"planner call failed: {type(exc).__name__}"
        latency_s = round(clock() - t0, 4)
        parsed = None
        if raw is not None:
            try:
                content = raw["choices"][0]["message"]["content"]
                tok = raw.get("usage") or {}
                usd = _usd(raw.get("model") or model, {"input": int(tok.get("prompt_tokens", 0)),
                                                        "output": int(tok.get("completion_tokens", 0))})
                parsed = _parse(json.loads(content))
            except Exception:
                parsed = None
            if parsed is None and fallback_reason is None:
                fallback_reason = "planner answer invalid or off-schema"
        if parsed is None:
            chosen = sweep_fallback(sweep_lows, high_cap,
                                    fallback_reason or "planner unusable: kept the sweep")
            fallback_reason = chosen.reason
        else:
            r, hi, lo, reason = parsed
            cr, chi, clo, clamped = clamp(r, hi, lo)
            chosen = PhaseThresholds(cr, chi, clo, source="spark", clamped=clamped, reason=reason)

    trace = {"kind": "phase_clock_thresholds", "model": model, "prompt_sha256": sha,
             "choice": {"prefill_ratio": chosen.prefill_ratio, "high_mhz": chosen.high_mhz,
                        "low_mhz": chosen.low_mhz},
             "source": chosen.source, "clamped": chosen.clamped,
             "latency_s": {"value": latency_s, "label": "MEASURED"} if latency_s is not None else None,
             "usd": {"value": usd, "label": "ESTIMATE"} if usd is not None else None,
             "fallback": chosen.source == "sweep",
             "fallback_reason": fallback_reason if chosen.source == "sweep" else None,
             "reason": chosen.reason}
    if on_trace is not None:
        on_trace(trace)
    return chosen


class MockSparkTransport:
    """A fixed, no-network Spark response for the rehearsal and tests.

    Returns valid thresholds built from the summary (prefill at the high cap, decode at the
    lowest sweep candidate), so the validate/clamp path runs without touching the network.
    """

    def __init__(self, *, prefill_ratio: float = 4.0, high_mhz: int | None = None,
                 low_mhz: int | None = None, model: str = MODEL) -> None:
        self.prefill_ratio = prefill_ratio
        self.high_mhz = high_mhz
        self.low_mhz = low_mhz
        self.model = model

    def __call__(self, body: dict) -> dict:
        summary = json.loads(body["messages"][-1]["content"])
        hi = self.high_mhz if self.high_mhz is not None else int(summary.get("high_cap_mhz", CLOCK_MAX))
        lows = summary.get("sweep_low_candidates_mhz") or [CLOCK_MIN]
        lo = self.low_mhz if self.low_mhz is not None else min(lows)
        content = json.dumps({"prefill_ratio": self.prefill_ratio, "high_mhz": hi, "low_mhz": lo,
                              "reason": f"decode is memory-bound; {lo} MHz holds p99 inside budget"})
        return {"model": self.model,
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 70, "completion_tokens": 40}}
