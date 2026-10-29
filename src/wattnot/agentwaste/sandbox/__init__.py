"""Code Mode sandbox runtime: run a candidate's task as one locked-down program and measure it.

Unlike :mod:`wattnot.agentwaste.codemode` (which *models* the Code Mode "after"), this package
actually runs the collapsed one-turn program in a locked-down subprocess and reports what the
run really cost -- labelling each field MEASURED / TOKENIZER / ESTIMATE honestly. No live model,
no network, no paid call.
"""

from __future__ import annotations

from wattnot.agentwaste.sandbox.run import (
    RECEIPT_SPEC,
    SPEC,
    build,
    build_receipt,
    run_candidate,
)
from wattnot.agentwaste.sandbox.runner import Limits, SandboxResult, run_code

__all__ = ["SPEC", "RECEIPT_SPEC", "build", "build_receipt", "run_candidate",
           "Limits", "SandboxResult", "run_code"]
