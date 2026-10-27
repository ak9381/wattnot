"""Approve before apply.

WattNot never changes a power cap, a vLLM setting, a routing policy or a grid shed on its own
say-so. It writes a proposal (what changes, the saving with its label, the quality and p99
checks, a preview of the receipt), and nothing is applied until a person approves it.

    from wattnot.approvals import Approvals, ApprovalGate

See docs/APPROVALS.md.
"""

from wattnot.approvals.appliers import (
    Applier,
    ApplyError,
    CallbackApplier,
    ConfigFileApplier,
    PowerCapApplier,
)
from wattnot.approvals.gate import ApprovalGate, NotApproved, TrialSession
from wattnot.approvals.model import ProposalError
from wattnot.approvals.policy import Policy, PolicyError
from wattnot.approvals.service import Approvals, StateError
from wattnot.approvals.store import NotFound, Store

__all__ = [
    "Applier", "ApplyError", "ApprovalGate", "Approvals", "CallbackApplier",
    "ConfigFileApplier", "NotApproved", "NotFound", "Policy", "PolicyError",
    "PowerCapApplier", "ProposalError", "StateError", "Store", "TrialSession",
]
