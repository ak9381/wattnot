"""p99 gate: the candidate's tail latency must stay inside the budget.

Budget is baseline p99 times (1 + slack), 10% slack by default. An explicit
SLA in seconds replaces the slack rule when given.
"""

from __future__ import annotations

from wattnot.gates.result import GateResult
from wattnot.runner.trial import TrialResult


def p99_gate(base: TrialResult, cand: TrialResult, *, slack: float = 0.10,
             sla_s: float | None = None) -> GateResult:
    if slack < 0:
        raise ValueError("slack must be >= 0")
    budget = sla_s if sla_s is not None else base.p99_s * (1 + slack)
    detail = {"baseline_p99_s": base.p99_s, "candidate_p99_s": cand.p99_s,
              "budget_s": budget, "slack": slack, "sla_s": sla_s}
    if cand.p99_s <= budget:
        return GateResult("p99", True, f"p99 {cand.p99_s:.3f}s within {budget:.3f}s", detail)
    return GateResult("p99", False, f"p99 {cand.p99_s:.3f}s over budget {budget:.3f}s", detail)
