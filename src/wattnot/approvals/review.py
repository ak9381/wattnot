"""The approve / reject / HOLD rule, shared by every approval path.

A reviewer verifies a change against a set of gate checks, each of which is True (passed),
False (really failed on real numbers), or None (skipped — no data). The rule, ported from the
Modal runner's ``verify_change`` so every path decides the same way:

* any check that really failed (ok is False) -> ``rejected``;
* otherwise any check that was skipped (ok is None: no data) -> ``held`` for a person, NOT approved
  and NOT rejected (a missing measurement must never silently approve or silently reject);
* otherwise (every check passed) -> ``approved``.

This is the one implementation. ``wattnot.e2e.live.review_change`` (the full-stack reviewer the
E2E / Modal / Baseten runners use) and the long-run driver's approval path both call it, so a
skipped check holds the change everywhere instead of being decided differently in each place.
"""

from __future__ import annotations

from collections.abc import Mapping

HELD = "held"
APPROVED = "approved"
REJECTED = "rejected"


def _ok_items(checks):
    """Normalise `checks` to (name, ok) pairs. Accepts a {name: ok} mapping, a {name: {"ok": ...}}
    mapping (the Modal verify_change shape), or an iterable of (name, ok) / check dicts."""
    if isinstance(checks, Mapping):
        for name, v in checks.items():
            yield name, (v.get("ok") if isinstance(v, Mapping) else v)
        return
    for c in checks:
        if isinstance(c, Mapping):
            yield c.get("check") or c.get("name"), c.get("ok")
        else:
            yield c[0], c[1]


def held_decision(checks) -> dict:
    """{"decision", "failed_checks", "skipped_checks"} from the gate checks (see the module rule)."""
    failed, skipped = [], []
    for name, ok in _ok_items(checks):
        if ok is False:
            failed.append(name)
        elif ok is None:
            skipped.append(name)
    decision = REJECTED if failed else HELD if skipped else APPROVED
    return {"decision": decision, "failed_checks": failed, "skipped_checks": skipped}


__all__ = ["held_decision", "HELD", "APPROVED", "REJECTED"]
