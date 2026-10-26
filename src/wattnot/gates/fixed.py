"""Fixed quality checks: exact, contains and regex, no model in the loop.

Each workload item may carry a `checks` list in its metadata, for example
`{"type": "regex", "pattern": "def slugify_title\\("}`. An item passes when
all its checks pass. Items with no checks are left out of the count.

The gate compares pass rates: the candidate must score at least the
baseline minus `max_drop_pts` percentage points (1 point by default).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from wattnot.gates.result import GateResult
from wattnot.runner.workload import WorkloadItem

CHECK_TYPES = ("exact", "contains", "regex")


@dataclass(frozen=True)
class Check:
    type: str
    value: str

    @classmethod
    def from_meta(cls, raw: Mapping) -> Check:
        kind = raw.get("type")
        if kind not in CHECK_TYPES:
            raise ValueError(f"unknown check type {kind!r}")
        value = raw.get("pattern" if kind == "regex" else "value")
        if not isinstance(value, str):
            raise ValueError(f"check {kind!r} needs a string value")
        return cls(kind, value)


def run_check(check: Check, output: str) -> bool:
    if check.type == "exact":
        return output.strip() == check.value.strip()
    if check.type == "contains":
        return check.value in output
    return re.search(check.value, output, flags=re.MULTILINE) is not None


def item_checks(item: WorkloadItem) -> list[Check]:
    return [Check.from_meta(raw) for raw in item.meta.get("checks", [])]


def pass_rate(outputs: Mapping[str, str], items: Iterable[WorkloadItem]) -> tuple[float, int]:
    """Percent of checked items that pass, and how many items were checked.

    A missing output counts as a fail, so a crashed request can't hide.
    """

    checked = passed = 0
    for item in items:
        checks = item_checks(item)
        if not checks:
            continue
        checked += 1
        text = outputs.get(item.id)
        if text is not None and all(run_check(c, text) for c in checks):
            passed += 1
    if checked == 0:
        return 0.0, 0
    return 100.0 * passed / checked, checked


def fixed_checks(base_outputs: Mapping[str, str], cand_outputs: Mapping[str, str],
                 items: Iterable[WorkloadItem], *, max_drop_pts: float = 1.0) -> GateResult:
    items = list(items)
    base, n = pass_rate(base_outputs, items)
    cand, _ = pass_rate(cand_outputs, items)
    detail = {"baseline_pct": round(base, 3), "candidate_pct": round(cand, 3),
              "checked": n, "max_drop_pts": max_drop_pts}
    if n == 0:
        return GateResult("fixed", False, "no items carry checks", detail)
    if cand >= base - max_drop_pts:
        return GateResult("fixed", True, f"pass rate {cand:.1f}% vs {base:.1f}%", detail)
    return GateResult("fixed", False,
                      f"pass rate fell to {cand:.1f}% from {base:.1f}%", detail)
