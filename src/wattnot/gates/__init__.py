"""Gates that decide whether a config change is safe to keep.

Fixed checks look at quality. The p99 gate looks at tail latency.
`decide` runs both and a change is applied only if every gate passes.
"""

from wattnot.gates.decide import Decision, decide, guarded_apply
from wattnot.gates.fixed import Check, fixed_checks, run_check
from wattnot.gates.latency import p99_gate
from wattnot.gates.result import GateResult

__all__ = [
    "Check",
    "Decision",
    "GateResult",
    "decide",
    "fixed_checks",
    "guarded_apply",
    "p99_gate",
    "run_check",
]
