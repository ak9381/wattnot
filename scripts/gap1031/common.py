"""Shared guard for the Oct 31 gap tests: one $3 job cap on real Meta API spend.

Every call goes through GuardedClient (written to the spend ledger before it is sent).
This job refuses a call once the ledger entries whose purpose starts with `gap-1031`
reach JOB_CAP_USD, so the gap tests together can never spend more than $3.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from wattnot.meta.client import GuardedClient  # noqa: E402
from wattnot.meta.ledger import SpendLedger  # noqa: E402

JOB_CAP_USD = 3.0
PREFIX = "gap-1031"
OUT = ROOT / "data" / "gap-1031"


def own_spend(ledger: SpendLedger) -> float:
    return sum(e.get("usd", 0) or 0 for e in ledger.entries()
               if str(e.get("purpose", "")).startswith(PREFIX))


class JobClient:
    """GuardedClient + this job's $3 cap. Refuses before sending if the cap would be crossed."""

    def __init__(self, reserve_usd: float = 0.05) -> None:
        self.ledger = SpendLedger()
        self.client = GuardedClient(ledger=self.ledger)
        self.reserve = reserve_usd

    def own(self) -> float:
        return own_spend(self.ledger)

    def __call__(self, body, *, purpose: str = PREFIX):
        if not purpose.startswith(PREFIX):
            purpose = f"{PREFIX}:{purpose}"
        if self.own() + self.reserve > JOB_CAP_USD:
            raise RuntimeError(f"gap-1031 job cap ${JOB_CAP_USD} reached")
        return self.client(body, purpose=purpose)
