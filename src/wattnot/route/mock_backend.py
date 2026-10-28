"""Deterministic mock escalation backend: no GPU, no model, no network.

Turns a list of prompt specs into chain items (one attempt per tier) so the three-tier
escalation chain can be exercised on CPU, in tests and in the RUN3 rehearsal. Every number is
MOCK. A prompt declares the lowest tier that answers it correctly (``solves_at``); tiers below
that produce an answer the reference-free gate rejects, so the chain escalates past them.

Energy per tier grows with model size (a bigger model burns more joules per answer). These are
illustrative fixed costs, labelled MOCK, never MEASURED.
"""

from __future__ import annotations

from collections.abc import Sequence

from wattnot.route.escalate import TIER_CHAIN

# Illustrative per-answer cost by tier (MOCK). Bigger model, more joules and more dollars.
TIER_JOULES = {"llama-8b": 10.0, "glimmer-30b": 28.0, "spark-open": 70.0}
TIER_USD = {"llama-8b": 0.00008, "glimmer-30b": 0.00022, "spark-open": 0.0019}

# A passing / failing answer per task kind, shaped so escalate.gate agrees.
_PASS = {"math": "Working it through, #### 42", "code": "```python\ndef f():\n    return 1\n```",
         "qa": "Paris"}
_FAIL = {"math": "it is roughly forty-something", "code": "```python\ndef f(:\n```",
         "qa": "Sorry, I can't help with that"}


def _attempt(name: str, kind: str, solved: bool) -> dict:
    """One tier's attempt. When ``solved`` the gate passes and the answer is correct."""

    return {"tier": name, "model": next(t["model"] for t in TIER_CHAIN if t["name"] == name),
            "text": (_PASS if solved else _FAIL).get(kind, _PASS["qa"] if solved else _FAIL["qa"]),
            "finish_reason": "stop", "correct": solved,
            "joules": TIER_JOULES.get(name, 10.0), "usd": TIER_USD.get(name, 0.0001),
            "label": "MOCK"}


def mock_item(prompt: dict, tiers: Sequence[dict] = TIER_CHAIN) -> dict:
    """Build one chain item from a prompt spec {id, kind, solves_at}.

    ``solves_at`` is the index of the lowest tier that gets it right (clamped to the last tier,
    which is terminal). Tiers before it fail the gate; that tier and beyond pass.
    """

    kind = prompt.get("kind", "qa")
    solves_at = min(int(prompt.get("solves_at", 0)), len(tiers) - 1)
    attempts = [_attempt(t["name"], kind, i >= solves_at) for i, t in enumerate(tiers)]
    return {"id": str(prompt.get("id", "")), "kind": kind, "attempts": attempts}


def mock_items(prompts: Sequence[dict], tiers: Sequence[dict] = TIER_CHAIN) -> list[dict]:
    return [mock_item(p, tiers) for p in prompts]


def sample_prompts(n: int = 20) -> list[dict]:
    """A deterministic spread of prompts: most solved by the small model, a few escalating.

    Roughly 70% answered by tier 0, 20% by tier 1, 10% by tier 2 — a realistic small-first mix.
    """

    kinds = ("math", "code", "qa")
    out = []
    for i in range(n):
        r = i % 10
        solves_at = 0 if r < 7 else (1 if r < 9 else 2)
        out.append({"id": f"p{i:03d}", "kind": kinds[i % 3], "solves_at": solves_at})
    return out
