"""Send the sweep chart to Muse Wonder, or fall back deterministically offline.

The live path inlines the rendered PNG as a data URI and asks Muse Wonder which of
the bounded candidate settings to try next — the model *reads the chart*. It returns
JSON only, constrained to the candidate list. Any failure (no key, over budget,
timeout, bad JSON, a setting that is not a candidate) falls back to the deterministic
``WonderSupervisor`` logic, which is already tested: it picks the next untried
candidate and stops when the $/quality Pareto front flattens. So the sweep advances
with or without the network, and the receipt records which path was taken.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from wattnot.meta.wonder import WonderSupervisor, wonder_trace_id
from wattnot.proof.wonder_planner import candidates as default_candidates
from wattnot.wonderchart.chart import EST_WH_CHART, ChartPoint, points_sha256

DEFAULT_MODEL = "muse-wonder-1.0"

_SYSTEM = (
    "You are Muse Wonder, an energy and cost supervisor. You are shown a scatter plot of an "
    "inference power sweep: x is joules per correct answer (lower is better), y is quality "
    "(0-1, higher is better). Green dots are settings the gates kept; red dots were rejected. "
    "Each dot is labelled with its power cap in watts. Pick the next setting to try from the "
    "allowed candidate list, pushing toward lower joules per correct answer while keeping "
    "quality, or choose action=stop if further trials are unlikely to improve the $/quality "
    "front. Never pick a setting already on the chart. One concise sentence of reason. JSON only."
)

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "cap_w", "clock_profile", "reason"],
    "properties": {
        "action": {"enum": ["try", "stop"]},
        "cap_w": {"type": ["integer", "null"]},
        "clock_profile": {"type": ["string", "null"]},
        "reason": {"type": "string", "maxLength": 240},
    },
}


@dataclass
class ChartSuggestion:
    """Wonder's (or the fallback's) next-setting suggestion."""

    action: str                       # "try" or "stop"
    cap_w: int | None
    clock_profile: str | None
    reason: str
    source: str                       # "wonder" or "fallback"
    trace_id: str
    model: str = DEFAULT_MODEL
    tokens: dict = field(default_factory=lambda: {"input": 0, "output": 0})


def _as_trials(points: Sequence[ChartPoint]) -> list[dict]:
    """ChartPoints -> the trial dicts ``WonderSupervisor`` scores. A rejected point
    gets quality 0 so it is never on the Pareto front (it cannot be the next pick)."""

    trials = []
    for p in points:
        trials.append({
            "cap_w": int(p.cap_w),
            "clock_profile": str(p.clock_profile),
            "joules_per_correct": float(p.joules_per_correct),
            "quality": float(p.quality) if p.kept else 0.0,
            "p99_delta": 0.0,
            # the supervisor's cost axis is energy per correct answer on a rented GPU
            "usd_per_1k": float(p.joules_per_correct),
            "label": str(p.label),
        })
    return trials


def _candidate_pairs(candidates: Sequence[tuple[int, str]] | None) -> list[tuple[int, str]]:
    return [(int(c), str(p)) for c, p in (candidates or default_candidates())]


def fallback_suggestion(points: Sequence[ChartPoint],
                        candidates: Sequence[tuple[int, str]] | None = None,
                        ) -> ChartSuggestion:
    """Deterministic offline pick, via the tested ``WonderSupervisor`` logic."""

    pairs = _candidate_pairs(candidates)
    sup = WonderSupervisor(mock=True, candidates=pairs)
    d = sup.next_trial(_as_trials(points))
    return ChartSuggestion(
        action="try" if d.action == "trial" else "stop",
        cap_w=d.cap_w,
        clock_profile=d.clock_profile,
        reason=f"offline fallback: {d.reason}"[:195],
        source="fallback",
        trace_id=d.trace_id,
        model="deterministic",
        tokens=dict(d.tokens),
    )


def _data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def read_chart(points: Sequence[ChartPoint], *, png: bytes | None = None,
               client: Callable[..., Mapping] | None = None,
               candidates: Sequence[tuple[int, str]] | None = None,
               model: str = DEFAULT_MODEL) -> ChartSuggestion:
    """Ask Muse Wonder to read the chart, else fall back deterministically.

    ``client`` is any ``(body, purpose=...) -> response`` callable (pass a
    ``wattnot.meta.client.GuardedClient`` for the live, guarded, paid path). With no
    client, or a rendered ``png`` missing, or any error, the deterministic fallback
    is used and the suggestion is marked ``source="fallback"``.
    """

    pairs = _candidate_pairs(candidates)
    if client is None or png is None:
        return fallback_suggestion(points, pairs)

    tried = {(p.cap_w, p.clock_profile) for p in points}
    available = [c for c in pairs if c not in tried]
    user = {
        "candidates": [{"cap_w": c, "clock_profile": p} for c, p in available],
        "points": [p.as_dict() for p in points],
    }
    body = {
        "model": model,
        "reasoning_effort": "low",
        "max_tokens": 3000,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": json.dumps(user, separators=(",", ":"))},
                {"type": "image_url", "image_url": {"url": _data_uri(png)}},
            ]},
        ],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "WonderChart", "schema": _SCHEMA,
                                            "strict": True}},
    }
    try:
        raw = client(body, purpose=f"wonder-chart:trials-{len(points)}")
        content = raw.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        parsed = json.loads(content)
        usage = raw.get("usage") or {}
        tokens = {"input": int(usage.get("prompt_tokens", 0)),
                  "output": int(usage.get("completion_tokens", 0))}
        action = parsed.get("action")
        if action == "stop":
            return ChartSuggestion("stop", None, None,
                                   str(parsed.get("reason", "Wonder stopped the sweep"))[:195],
                                   "wonder", wonder_trace_id(parsed), model, tokens)
        cap, prof = parsed.get("cap_w"), parsed.get("clock_profile")
        if (cap, prof) not in available:
            # a setting Wonder is not allowed to pick -> fall back deterministically
            return fallback_suggestion(points, pairs)
        return ChartSuggestion("try", int(cap), str(prof),
                               str(parsed.get("reason", "Wonder read the chart"))[:195],
                               "wonder", wonder_trace_id(parsed), model, tokens)
    except Exception:  # noqa: BLE001 - any failure must still advance the sweep offline
        return fallback_suggestion(points, pairs)


class WonderChartReader:
    """Convenience wrapper binding a client, model and candidate list."""

    def __init__(self, *, client: Callable[..., Mapping] | None = None,
                 candidates: Sequence[tuple[int, str]] | None = None,
                 model: str = DEFAULT_MODEL) -> None:
        self.client = client
        self.candidates = _candidate_pairs(candidates)
        self.model = model

    def read(self, points: Sequence[ChartPoint], *, png: bytes | None = None) -> ChartSuggestion:
        return read_chart(points, png=png, client=self.client,
                          candidates=self.candidates, model=self.model)


def wonder_chart_entry(points: Sequence[ChartPoint], suggestion: ChartSuggestion, *,
                       chart_sha256: str | None = None, est_wh: float = EST_WH_CHART,
                       label: str = "SYNTHETIC") -> dict:
    """Shape the signed-receipt ``measurement.wonder_chart`` block.

    Numbers, short ids and settings only (lint-clean, signature-covered). The chart
    itself is referenced by hash — the PNG never goes into the public receipt. When no
    PNG was rendered the hash covers the plotted data instead, so a pointer always exists.
    """

    pts = list(points)
    return {
        "label": label,
        "chart_sha256": str(chart_sha256 or points_sha256(pts)),
        "source": str(suggestion.source)[:16],
        "offline_fallback": suggestion.source == "fallback",
        "model": str(suggestion.model)[:64],
        "suggestion": {
            "action": str(suggestion.action)[:8],
            "cap_w": int(suggestion.cap_w) if suggestion.cap_w is not None else None,
            "clock_profile": (str(suggestion.clock_profile)[:32]
                              if suggestion.clock_profile is not None else None),
        },
        "reason": str(suggestion.reason)[:195],
        "trace_id": str(suggestion.trace_id)[:80],
        "est_wh": {"value": round(float(est_wh), 6), "label": "ESTIMATE"},
        "points": [p.as_dict() for p in pts],
    }


__all__ = [
    "DEFAULT_MODEL",
    "ChartSuggestion",
    "WonderChartReader",
    "fallback_suggestion",
    "read_chart",
    "wonder_chart_entry",
]
