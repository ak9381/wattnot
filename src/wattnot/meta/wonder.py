"""Muse Wonder supervisor: Pareto-optimal cap and clock search.

Supervisor for inference power and clock tuning. Muse Wonder receives compact per-trial
summaries (cap_w, clock_profile, joules_per_correct, quality, p99_delta, usd_per_1k, label)
— never raw telemetry. It proposes the next (cap_w, clock_profile) setting from a bounded
candidate list, and halts deterministically when the $/quality Pareto front flattens.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wattnot.meta.client import GuardedClient
from wattnot.meta.ledger import SpendLedger

DEFAULT_MODEL = "muse-wonder"
DEFAULT_CANDIDATES: list[tuple[int, str]] = [
    (400, "base"),
    (350, "locked-high"),
    (300, "locked-mid"),
    (250, "locked-low"),
    (200, "locked-low"),
]

WONDER_SYSTEM = (
    "You are Muse Wonder, an energy and cost supervisor for inference workloads. You are given "
    "compact trial summaries (cap_w, clock profile, joules_per_correct, quality, p99 delta, "
    "usd_per_1k, label) and a candidate list of (cap_w, clock profile) pairs. Pick the next trial "
    "from the allowed candidates, or choose action=stop if further trials are unlikely to improve "
    "the $/quality Pareto front. Never repeat a trial already evaluated. The reason is one concise "
    "sentence citing key trade-offs. JSON only."
)

WONDER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "reason"],
    "properties": {
        "action": {"enum": ["trial", "stop"]},
        "cap_w": {"type": ["integer", "null"]},
        "clock_profile": {"type": ["string", "null"]},
        "reason": {"type": "string", "maxLength": 240},
    },
}


def normalize_trial(trial: Mapping) -> dict[str, Any]:
    """Normalize trial dictionary keys (supporting space or underscore)."""
    cap_w = trial.get("cap_w")
    clock_profile = trial.get("clock_profile") or trial.get("clock profile", "base")
    joules = trial.get("joules_per_correct")
    if joules is None:
        joules = trial.get("joules per correct", 0.0)
    quality = trial.get("quality", 1.0)
    p99_delta = trial.get("p99_delta")
    if p99_delta is None:
        p99_delta = trial.get("p99 delta", 0.0)
    usd_per_1k = trial.get("usd_per_1k")
    if usd_per_1k is None:
        usd_per_1k = trial.get("usd per 1k", 0.0)
    label = trial.get("label", "MOCK")

    return {
        "cap_w": int(cap_w) if cap_w is not None else 400,
        "clock_profile": str(clock_profile),
        "joules_per_correct": float(joules),
        "quality": float(quality),
        "p99_delta": float(p99_delta),
        "usd_per_1k": float(usd_per_1k),
        "label": str(label),
    }


def pareto_front(trials: Sequence[Mapping]) -> list[dict[str, Any]]:
    """Return non-dominated trials minimizing usd_per_1k and maximizing quality.

    Point A dominates point B if:
    usd_per_1k(A) <= usd_per_1k(B) and quality(A) >= quality(B)
    with at least one strict inequality.
    """
    normalized = [normalize_trial(t) for t in trials]
    front: list[dict[str, Any]] = []

    for cand in normalized:
        dominated = False
        c_cost, c_qual = cand["usd_per_1k"], cand["quality"]
        for other in normalized:
            o_cost, o_qual = other["usd_per_1k"], other["quality"]
            if (o_cost <= c_cost and o_qual >= c_qual) and (o_cost < c_cost or o_qual > c_qual):
                dominated = True
                break
        if not dominated and cand not in front:
            front.append(cand)

    front.sort(key=lambda x: (x["usd_per_1k"], -x["quality"]))
    return front


def pareto_efficiency_score(trials: Sequence[Mapping]) -> float:
    """Best quality per dollar from the Pareto front."""
    front = pareto_front(trials)
    if not front:
        return 0.0
    return max(t["quality"] / max(t["usd_per_1k"], 1e-9) for t in front)


def pareto_flattened(
    history: Sequence[Mapping],
    threshold: float = 0.01,
    patience: int = 3,
) -> tuple[bool, float, str]:
    """Deterministic local check: front improvement < threshold over last N trials.

    Enforced locally regardless of model suggestions.
    """
    if len(history) < patience:
        return False, 1.0, f"History length {len(history)} < patience {patience}"

    earlier = history[:-patience]
    if not earlier:
        return False, 1.0, "Insufficient baseline trials before window"

    score_earlier = pareto_efficiency_score(earlier)
    score_now = pareto_efficiency_score(history)

    if score_earlier <= 0:
        rel_improvement = 1.0 if score_now > 0 else 0.0
    else:
        rel_improvement = max(0.0, (score_now - score_earlier) / score_earlier)

    flattened = rel_improvement < threshold
    if flattened:
        reason = (
            f"$/quality Pareto front flattened: improvement {rel_improvement:.2%} < "
            f"threshold {threshold:.2%} over last {patience} trials"
        )
    else:
        reason = (
            f"Pareto front still improving: {rel_improvement:.2%} >= threshold {threshold:.2%}"
        )

    return flattened, rel_improvement, reason


def wonder_trace_id(payload: Mapping | Sequence) -> str:
    """Stable trace id for Wonder supervisor decisions."""
    blob = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return "wonder-" + hashlib.sha256(blob).hexdigest()[:16]


def calculate_per_model_spend(ledger: SpendLedger | None = None) -> dict[str, dict[str, Any]]:
    """Tally spend per model across spark, wonder, glimmer, llama, and sam."""
    categories = {"spark": 0.0, "wonder": 0.0, "glimmer": 0.0, "llama": 0.0, "sam": 0.0}

    if ledger is not None and ledger.path.exists():
        for entry in ledger.entries():
            m = str(entry.get("model", "")).lower()
            p = str(entry.get("purpose", "")).lower()
            usd = float(entry.get("usd", 0.0))
            if "spark" in m or "spark" in p:
                categories["spark"] += usd
            elif "wonder" in m or "wonder" in p:
                categories["wonder"] += usd
            elif "glimmer" in m or "glimmer" in p:
                categories["glimmer"] += usd
            elif "llama" in m or "llama" in p:
                categories["llama"] += usd
            elif "sam" in m or "sam" in p:
                categories["sam"] += usd
            else:
                categories["spark"] += usd

    return {
        k: {
            "usd": round(v, 6),
            "label": "MEASURED" if v > 0 and ledger is not None else "MOCK",
        }
        for k, v in categories.items()
    }


@dataclass
class SupervisorDecision:
    action: str  # "trial" or "stop"
    cap_w: int | None
    clock_profile: str | None
    reason: str
    trace_id: str
    tokens: dict[str, int]
    stop_flattened: bool = False


class WonderSupervisor:
    """Muse Wonder supervisor for cap and clock search."""

    def __init__(
        self,
        *,
        client: GuardedClient | None = None,
        model: str = DEFAULT_MODEL,
        candidates: Sequence[tuple[int, str]] | None = None,
        threshold: float = 0.01,
        patience: int = 3,
        mock: bool | None = None,
    ) -> None:
        self.model = model
        self.candidates = list(candidates or DEFAULT_CANDIDATES)
        self.threshold = float(threshold)
        self.patience = int(patience)
        self.trace_ids: list[str] = []

        if mock is not None:
            self.mock = bool(mock)
        else:
            env_mock = os.environ.get("WATTNOT_META_MOCK", "1").strip().lower()
            self.mock = env_mock not in ("0", "false", "no")

        self.client = client or (GuardedClient() if not self.mock else None)

    def next_trial(self, history: Sequence[Mapping]) -> SupervisorDecision:
        """Pick next trial from candidate list or stop if Pareto front has flattened."""
        normalized = [normalize_trial(t) for t in history]

        # 1. Deterministic local Pareto check (always enforced regardless of model)
        flattened, improvement, flat_reason = pareto_flattened(
            normalized, threshold=self.threshold, patience=self.patience
        )
        if flattened:
            tid = wonder_trace_id({"event": "stop_flattened", "trials": len(normalized)})
            self.trace_ids.append(tid)
            return SupervisorDecision(
                action="stop",
                cap_w=None,
                clock_profile=None,
                reason=flat_reason,
                trace_id=tid,
                tokens={"input": 0, "output": 0},
                stop_flattened=True,
            )

        # 2. Check candidate availability
        tried_pairs = {(t["cap_w"], t["clock_profile"]) for t in normalized}
        available = [c for c in self.candidates if c not in tried_pairs]
        if not available:
            tid = wonder_trace_id({"event": "stop_exhausted", "trials": len(normalized)})
            self.trace_ids.append(tid)
            return SupervisorDecision(
                action="stop",
                cap_w=None,
                clock_profile=None,
                reason="All bounded candidate cap/clock pairs exhausted (evaluated)",
                trace_id=tid,
                tokens={"input": 0, "output": 0},
                stop_flattened=False,
            )

        # 3. Model decision (or deterministic mock)
        if self.mock or self.client is None:
            cand_cap, cand_prof = available[0]
            tid = wonder_trace_id(
                {"event": "trial_mock", "cap": cand_cap, "clock": cand_prof, "n": len(normalized)}
            )
            self.trace_ids.append(tid)
            return SupervisorDecision(
                action="trial",
                cap_w=cand_cap,
                clock_profile=cand_prof,
                reason=f"MOCK: selected next candidate {cand_cap}W ({cand_prof})",
                trace_id=tid,
                tokens={"input": 0, "output": 0},
            )

        # Live call via GuardedClient
        payload = {
            "candidates": [{"cap_w": c[0], "clock_profile": c[1]} for c in available],
            "history": normalized,
            "threshold": self.threshold,
            "patience": self.patience,
        }
        body = {
            "model": self.model,
            "reasoning_effort": "low",
            "max_tokens": 3000,
            "messages": [
                {"role": "system", "content": WONDER_SYSTEM},
                {"role": "user", "content": json.dumps(payload, separators=(",", ":"))},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "WonderDecision", "schema": WONDER_SCHEMA, "strict": True},
            },
        }

        raw = self.client(body, purpose=f"wonder-supervisor:trials-{len(normalized)}")
        choice = raw.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        parsed = json.loads(choice)
        usage = raw.get("usage") or {}
        tokens = {
            "input": int(usage.get("prompt_tokens", 0)),
            "output": int(usage.get("completion_tokens", 0)),
        }
        tid = wonder_trace_id(parsed)
        self.trace_ids.append(tid)

        if parsed.get("action") == "stop":
            return SupervisorDecision(
                action="stop",
                cap_w=None,
                clock_profile=None,
                reason=parsed.get("reason", "Wonder supervisor stopped trials"),
                trace_id=tid,
                tokens=tokens,
            )

        cap = parsed.get("cap_w")
        prof = parsed.get("clock_profile")
        if (cap, prof) not in available:
            # Fall back safely to first valid candidate
            cap, prof = available[0]

        return SupervisorDecision(
            action="trial",
            cap_w=cap,
            clock_profile=prof,
            reason=parsed.get("reason", "Wonder selected next candidate"),
            trace_id=tid,
            tokens=tokens,
        )

    def write_summary(
        self,
        history: Sequence[Mapping],
        out_dir: str | Path,
        stop_reason: str,
        ledger: SpendLedger | None = None,
    ) -> tuple[Path, Path]:
        """Write efficiency summary JSON and Markdown."""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)

        normalized = [normalize_trial(t) for t in history]
        front = pareto_front(normalized)
        spend = calculate_per_model_spend(ledger)

        best = front[0] if front else (normalized[0] if normalized else None)

        json_doc = {
            "supervisor": "muse-wonder",
            "model": self.model,
            "trials_count": len(normalized),
            "stop_reason": stop_reason,
            "wonder_trace_ids": list(self.trace_ids),
            "per_model_spend": spend,
            "pareto_front": front,
            "trials": normalized,
            "best_trial": best,
            "threshold": self.threshold,
            "patience": self.patience,
        }

        json_path = out / "efficiency_summary.json"
        json_path.write_text(json.dumps(json_doc, indent=2) + "\n", encoding="utf-8")

        md_lines = [
            "# Muse Wonder Supervisor Efficiency Summary",
            "",
            f"- **Trials Evaluated**: {len(normalized)} (label: MEASURED/SIMULATED)",
            f"- **Stop Reason**: {stop_reason}",
            f"- **Pareto Front Count**: {len(front)}",
            f"- **Threshold**: {self.threshold:.2%}",
            f"- **Patience**: {self.patience} trials",
            "",
            "## Per-Model Spend",
            "",
            "| Model Role | Spend (USD) | Accounting Label |",
            "|:---|---:|:---|",
        ]
        for role, row in sorted(spend.items()):
            md_lines.append(f"| {role:<10} | ${row['usd']:.6f} | {row['label']} |")

        md_lines.extend([
            "",
            "## Pareto-Optimal Trials",
            "",
            "| Cap (W) | Clock Profile | Joules/Correct | Quality | USD/1k | Label |",
            "|---:|:---|---:|---:|---:|:---|",
        ])
        for t in front:
            md_lines.append(
                f"| {t['cap_w']} W | {t['clock_profile']} | {t['joules_per_correct']:.3f} J | "
                f"{t['quality']:.3f} | ${t['usd_per_1k']:.6f} | {t['label']} |"
            )

        if best:
            md_lines.extend([
                "",
                "## Optimal Operating Point",
                "",
                f"- **Cap**: {best['cap_w']} W (label: {best['label']})",
                f"- **Clock Profile**: {best['clock_profile']}",
                f"- **Quality**: {best['quality']:.3f} (label: {best['label']})",
                f"- **Cost**: ${best['usd_per_1k']:.6f} per 1k (label: {best['label']})",
            ])

        md_lines.append("")
        md_path = out / "efficiency_summary.md"
        md_path.write_text("\n".join(md_lines), encoding="utf-8")

        return json_path, md_path


__all__ = [
    "DEFAULT_CANDIDATES",
    "DEFAULT_MODEL",
    "SupervisorDecision",
    "WonderSupervisor",
    "calculate_per_model_spend",
    "normalize_trial",
    "pareto_flattened",
    "pareto_front",
    "wonder_trace_id",
]
