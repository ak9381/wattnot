"""Who picks the next cap to try.

MusePlanner asks Muse Spark through the spend guard (wattnot.meta). It only proposes: the
answer must be one of the allowed caps not tried yet, and the quality / p99 / energy gate in
session.py still decides whether a cap is accepted. If the API fails or the answer is off the
list, BisectPlanner picks instead and the log says so.
What is sent: the GPU's cap range, the gate rules, and per-cap numbers measured so far.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from wattnot.meta.agent import MODEL, _body, _json, usage_of
from wattnot.meta.ledger import cost_usd

STEP_W = 25
EXPLANATION_MAX = 280


def allowed_caps(lo: int, hi: int, tried: Sequence[int]) -> list[int]:
    start = ((lo + STEP_W - 1) // STEP_W) * STEP_W
    return [w for w in range(start, hi, STEP_W) if w not in set(tried)]


class BisectPlanner:
    """Deterministic fallback: step down while a cap is accepted, then bisect the gap."""

    name = "bisect"

    def __init__(self, lo: int = 300, hi: int = 700) -> None:
        self.lo, self.hi = lo, hi

    def propose(self, history: Sequence[dict]) -> dict:
        tried = [h["cap_w"] for h in history]
        free = allowed_caps(self.lo, self.hi, tried)
        if not free:
            return {"kind": "stop", "cap_w": None, "reason": "every allowed cap was tried"}
        ok = [h["cap_w"] for h in history if h["accepted"]]
        bad = [h["cap_w"] for h in history if not h["accepted"]]
        if not history:
            pick = min(free, key=lambda w: abs(w - 500))
        elif not bad:
            pick = min(free, key=lambda w: abs(w - max(self.lo, min(ok) - 100)))
        else:
            floor_ok = min(ok) if ok else self.hi
            top_bad = max((b for b in bad if b < floor_ok), default=self.lo)
            gap = [w for w in free if top_bad < w < floor_ok]
            if not gap:
                return {"kind": "stop", "cap_w": None, "reason": "gap between accepted and "
                        "rejected caps is closed"}
            pick = gap[len(gap) // 2]
        return {"kind": "try", "cap_w": pick, "reason": "bisect fallback"}


SYSTEM = (
    "You tune a GPU power cap for lower energy per request without breaking quality or p99 "
    "latency. One H100 serves Llama 3.1 8B. A cap is accepted only when ALL hold: the pass rate "
    "on a fixed answer-checked task set drops by at most 2 points, mean p99 latency rises by at "
    "most 10%, and the 95% confidence interval of the J/request change is below zero. Each trial "
    "is measured in paired, interleaved runs. You get the cap range, the caps still allowed and "
    "the trials so far. Pick ONE next cap from `allowed` (kind=try), or kind=stop when more trials "
    "are unlikely to find a lower accepted cap. Look for the lowest-energy accepted cap; the "
    "energy saving usually grows as the cap falls until p99 breaks the limit. The reason is one "
    "short line quoting the number that drove the choice. If `room` is present it is advice from "
    "a rack-photo scan (hot rack, suggested cap ceiling); weigh it, the gates still decide. Also "
    "give `explanation`: one or two plain sentences a non-expert can read, saying which cap you "
    "picked and which number drove it. JSON only."
)
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["kind", "cap_w", "reason", "explanation"],
          "properties": {"kind": {"enum": ["try", "stop"]}, "cap_w": {"type": ["integer", "null"]},
                         "reason": {"type": "string", "maxLength": 240},
                         "explanation": {"type": ["string", "null"], "maxLength": EXPLANATION_MAX}}}


def build_prompt(history: Sequence[dict], lo: int, hi: int,
                 context: dict | None = None) -> tuple[str, dict]:
    """The exact (system, payload) sent to Muse Spark: gate rules, allowed caps, trials.

    Numbers only: cap range, the caps still allowed, and per-cap measured deltas so far.
    No prompts, no model outputs, no secrets.
    """

    tried = [h["cap_w"] for h in history]
    payload = {"range_w": [lo, hi], "default_w": hi,
               "allowed": allowed_caps(lo, hi, tried),
               "trials": [{k: h[k] for k in ("cap_w", "energy_change_pct", "p99_change_pct",
                                             "quality_pass_pct", "stock_pass_pct", "accepted",
                                             "why_not")} for h in history],
               "prior": "an earlier unchecked run saw about -18% J/request at 400 W with p99 "
                        "+3.7%; treat it as a hint, not a fact"}
    if context:
        payload["room"] = context
    return SYSTEM, payload


def clean_explanation(value: object) -> str | None:
    """One whitespace-collapsed line, at most EXPLANATION_MAX chars. None when empty."""

    text = " ".join(str(value or "").split())
    if not text:
        return None
    return text[:EXPLANATION_MAX]


def call_usd(model: str, usage: dict) -> float:
    """USD for one Muse call from the published rates (input + output tokens)."""

    return round(cost_usd(model, int(usage.get("input", 0)), int(usage.get("output", 0))), 6)


class MusePlanner:
    name = "muse-spark"

    def __init__(self, client: Callable[..., dict], *, lo: int = 300, hi: int = 700,
                 fallback: BisectPlanner | None = None, model: str = MODEL) -> None:
        self.client, self.lo, self.hi, self.model = client, lo, hi, model
        self.fallback = fallback or BisectPlanner(lo, hi)
        self.last_usage: dict = {}
        self.last_error: str | None = None
        self.context: dict = {}  # extra prompt facts, e.g. the room scan hint

    def propose(self, history: Sequence[dict]) -> dict:
        free = allowed_caps(self.lo, self.hi, [h["cap_w"] for h in history])
        if not free:
            return {"kind": "stop", "cap_w": None, "reason": "every allowed cap was tried",
                    "by": self.name}
        system, payload = build_prompt(history, self.lo, self.hi, self.context)
        try:
            raw = self.client(_body(system, payload, SCHEMA, "NextCap", effort="low",
                                    max_tokens=4000), purpose="next-power-cap")
            usage = usage_of(raw)
            self.last_usage, self.last_error = usage, None
        except Exception as e:  # noqa: BLE001 - no answer came back, so bisect picks
            self.last_error = f"{type(e).__name__}: {str(e)[:160]}"
            out = self.fallback.propose(history)
            return {**out, "by": "bisect-fallback", "muse_error": self.last_error,
                    "proposal_accepted": None, "explanation": None, "usage": None,
                    "model": self.model, "usd": None}
        try:
            d = _json(raw)
            reason = " ".join(str(d.get("reason", "")).split())[:240]
            explanation = clean_explanation(d.get("explanation"))
            usage = dict(usage)
            usd = call_usd(self.model, usage)
            if d.get("kind") == "stop":
                return {"kind": "stop", "cap_w": None, "reason": reason, "by": self.name,
                        "proposal_accepted": True, "explanation": explanation,
                        "usage": usage, "model": self.model, "usd": usd}
            if d.get("cap_w") not in free:
                raise ValueError(f"off the allow-list: {d.get('cap_w')!r}")
            return {"kind": "try", "cap_w": int(d["cap_w"]), "reason": reason, "by": self.name,
                    "proposal_accepted": True, "explanation": explanation,
                    "usage": usage, "model": self.model, "usd": usd}
        except Exception as e:  # noqa: BLE001 - the answer failed validation, so bisect picks
            self.last_error = f"{type(e).__name__}: {str(e)[:160]}"
            out = self.fallback.propose(history)
            return {**out, "by": "bisect-fallback", "muse_error": self.last_error,
                    "proposal_accepted": False, "explanation": None, "usage": None,
                    "model": self.model, "usd": None}
