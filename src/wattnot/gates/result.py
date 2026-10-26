"""The shape every gate returns."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GateResult:
    """One gate's verdict. `detail` holds the numbers behind it."""

    gate: str
    passed: bool
    reason: str
    detail: dict = field(default_factory=dict)

    def event_data(self, trial_id: str) -> dict:
        return {
            "trial_id": trial_id,
            "gate": self.gate,
            "passed": self.passed,
            "reason": self.reason,
            "detail": self.detail,
        }
