"""Run the gates for one trial and apply or roll back the change.

Every gate writes a `gate.result` event. If all pass, `apply()` is called
and `action.applied` is written. If any fails, `revert()` is called and
`action.rolled_back` is written. Quality runs first; the p99 gate still
runs after a quality fail so the log shows the full picture.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from wattnot.gates.fixed import fixed_checks
from wattnot.gates.latency import p99_gate
from wattnot.gates.noise import noise_gate
from wattnot.gates.result import GateResult
from wattnot.runner.trial import TrialResult
from wattnot.runner.workload import WorkloadItem


@dataclass(frozen=True)
class Decision:
    trial_id: str
    passed: bool
    results: tuple[GateResult, ...]

    @property
    def failed(self) -> list[str]:
        return [r.gate for r in self.results if not r.passed]


def decide(base: TrialResult, cand: TrialResult, base_outputs: Mapping[str, str],
           cand_outputs: Mapping[str, str], items: list[WorkloadItem], *,
           slack: float = 0.10, sla_s: float | None = None, max_drop_pts: float = 1.0,
           base_wh: list[float] | None = None, cand_wh: list[float] | None = None,
           vision: GateResult | None = None, hotspot: GateResult | None = None,
           spoken: GateResult | None = None, emit=None) -> Decision:
    """With per-rep Wh lists, the noise gate runs third (Welch + bootstrap CI).

    ``vision`` is an optional pre-computed eye-test ``GateResult`` (see
    ``wattnot.vision.vision_gate``). When given it joins the gate set, so a
    candidate the served vision model misreads is rejected and rolled back to
    the last safe config just like a quality or latency failure.

    ``hotspot`` is an optional pre-computed SAM 3.1 hotspot-set ``GateResult``
    (see ``wattnot.sam.hotspot_set.hotspot_set_gate``). When given it joins the
    gate set the same way, so a candidate that breaks the hotspot detector on
    the SYNTHETIC scenes is rejected and rolled back.

    ``spoken`` is an optional pre-computed watts-per-spoken-answer ``GateResult``
    (see ``wattnot.spokenanswer.spoken_answer_gate``). When given it joins the
    gate set the same way, so a candidate the served model (Glimmer) mishears on
    the SYNTHETIC recorded questions is rejected and rolled back, exactly like
    the eye test.
    """

    results = (
        fixed_checks(base_outputs, cand_outputs, items, max_drop_pts=max_drop_pts),
        p99_gate(base, cand, slack=slack, sla_s=sla_s),
    )
    if base_wh is not None and cand_wh is not None:
        results = (*results, noise_gate(base_wh, cand_wh))
    if vision is not None:
        results = (*results, vision)
    if hotspot is not None:
        results = (*results, hotspot)
    if spoken is not None:
        results = (*results, spoken)
    if emit is not None:
        for r in results:
            emit.emit("gate.result", r.event_data(cand.trial_id),
                      step_id=f"gate:{cand.trial_id}:{r.gate}")
    return Decision(cand.trial_id, all(r.passed for r in results), results)


def guarded_apply(decision: Decision, *, apply: Callable[[], None],
                  revert: Callable[[], None], change: dict, emit=None) -> bool:
    """Apply the change only on a full pass. Returns True when applied."""

    step = f"action:{decision.trial_id}"
    if decision.passed:
        apply()
        if emit is not None:
            emit.emit("action.applied", {"trial_id": decision.trial_id, "change": change},
                      step_id=step)
        return True
    revert()
    if emit is not None:
        emit.emit("action.rolled_back", {"trial_id": decision.trial_id, "change": change,
                                         "failed_gates": decision.failed}, step_id=step)
    return False
