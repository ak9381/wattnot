"""Real Muse Spark usage on the Meta Model API, with a hard spend cap.

* `ledger`  persistent spend ledger and the guard that refuses calls over budget
* `client`  Chat Completions client that goes through the guard
* `agent`   WattNot's agent decisions (next knob, receipt notes, effort order)
* `cli`     `wattnot meta spend|check|session`
"""

from __future__ import annotations

from wattnot.meta.ledger import (
    CAP_USD,
    SOFT_USD,
    BudgetExceeded,
    SpendLedger,
    cost_usd,
    price_for,
)

__all__ = ["CAP_USD", "SOFT_USD", "BudgetExceeded", "SpendLedger", "cost_usd", "price_for"]
