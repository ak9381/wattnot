"""Gates that decide whether a config change is safe to keep.

Fixed checks look at quality. The p99 gate looks at tail latency. The noise
gate checks the saving is real (Welch test + bootstrap CI).
`decide` runs both and a change is applied only if every gate passes.
"""

from wattnot.gates.decide import Decision, decide, guarded_apply
from wattnot.gates.fixed import Check, fixed_checks, run_check
from wattnot.gates.latency import p99_gate
from wattnot.gates.noise import Welch, bootstrap_saving_ci, noise_gate, welch
from wattnot.gates.result import GateResult

__all__ = [
    "Check",
    "Decision",
    "GateResult",
    "Welch",
    "bootstrap_saving_ci",
    "decide",
    "fixed_checks",
    "guarded_apply",
    "noise_gate",
    "p99_gate",
    "run_check",
    "welch",
]
