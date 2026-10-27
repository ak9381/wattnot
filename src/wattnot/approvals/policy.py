"""Optional auto-approve policy. Off unless a human turns it on with explicit thresholds.

The policy file is `<root>/policy.json`. If it is missing, or `enabled` is false, nothing is
ever approved automatically. When it is enabled, a proposal is approved without a human only
if every rule below holds. Anything else stays pending for a person.

    {
      "format": "wattnot.auto-approve/1",
      "enabled": true,
      "allowed_labels": ["MEASURED"],         // SIMULATED and MOCK can never be listed
      "kinds": {
        "power_cap": {"min_saving_pct": 5, "max_p99_increase_pct": 10, "max_cap_drop_pct": 25},
        "cloud_policy": {"min_saving_pct": 20, "max_p99_increase_pct": 10}
      },
      "max_per_day": 5
    }

Every enabled kind must set `min_saving_pct` and `max_p99_increase_pct`; there are no
defaults, so turning auto-approve on is always a deliberate choice. A quality check that did
not pass, a missing p99 check, or a saving that is not labelled MEASURED (by default) all
keep the proposal pending.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from wattnot.approvals import model as m

FORMAT = "wattnot.auto-approve/1"
# Labels that must never be auto-approved: nothing real was measured.
NEVER_AUTO = ("SIMULATED", "MOCK")
REQUIRED = ("min_saving_pct", "max_p99_increase_pct")
ALLOWED_RULE_KEYS = (*REQUIRED, "max_cap_drop_pct", "max_target_pct")


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class Policy:
    enabled: bool = False
    allowed_labels: tuple[str, ...] = ("MEASURED",)
    kinds: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    max_per_day: int | None = None

    @staticmethod
    def off() -> Policy:
        return Policy()

    @staticmethod
    def from_dict(d: Mapping) -> Policy:
        if d.get("format", FORMAT) != FORMAT:
            raise PolicyError(f"unknown policy format {d.get('format')!r}")
        labels = tuple(d.get("allowed_labels") or ("MEASURED",))
        bad = [x for x in labels if x in NEVER_AUTO or x not in m.LABELS]
        if bad:
            raise PolicyError(f"allowed_labels cannot include {', '.join(bad)}: "
                              "only MEASURED or ESTIMATE can be auto-approved")
        kinds = d.get("kinds") or {}
        for kind, rules in kinds.items():
            if kind not in m.KINDS:
                raise PolicyError(f"unknown kind {kind!r}")
            if kind == m.TRIAL_GRANT:
                raise PolicyError("trial_grant cannot be auto-approved; a person approves "
                                  "permission to touch the hardware")
            unknown = set(rules) - set(ALLOWED_RULE_KEYS)
            if unknown:
                raise PolicyError(f"{kind}: unknown rule {', '.join(sorted(unknown))}")
            if bool(d.get("enabled")):
                missing = [r for r in REQUIRED if not isinstance(rules.get(r), int | float)]
                if missing:
                    raise PolicyError(f"{kind}: set {', '.join(missing)} (no defaults)")
        if bool(d.get("enabled")) and not kinds:
            raise PolicyError("enabled needs at least one kind with thresholds")
        cap = d.get("max_per_day")
        if cap is not None and (not isinstance(cap, int) or cap < 1):
            raise PolicyError("max_per_day must be a whole number >= 1")
        return Policy(bool(d.get("enabled", False)), labels,
                      {k: dict(v) for k, v in kinds.items()}, cap)

    def to_dict(self) -> dict:
        return {"format": FORMAT, "enabled": self.enabled,
                "allowed_labels": list(self.allowed_labels),
                "kinds": {k: dict(v) for k, v in self.kinds.items()},
                **({"max_per_day": self.max_per_day} if self.max_per_day else {})}

    def sha256(self) -> str:
        return m.sha256_hex(m.canon(self.to_dict()))

    def evaluate(self, p: Mapping, *, approved_today: int = 0) -> tuple[bool, list[str]]:
        """(ok, reasons). reasons says why not, or lists what held."""
        if not self.enabled:
            return False, ["auto-approve is off"]
        if p.get("scope") == m.TRIAL or p["kind"] == m.TRIAL_GRANT:
            return False, ["a trial grant is never auto-approved"]
        rules = self.kinds.get(p["kind"])
        if rules is None:
            return False, [f"{p['kind']} is not in the auto-approve list"]
        why: list[str] = []
        labels = [s["label"] for s in p.get("savings", [])]
        if not labels:
            why.append("no saving is stated")
        off = sorted({x for x in labels if x not in self.allowed_labels})
        if off:
            why.append(f"label {', '.join(off)} is not allowed for auto-approve")
        checks = p.get("checks", {})
        if (checks.get("quality") or {}).get("passed") is not True:
            why.append("the quality check did not pass (or was not run)")
        p99 = checks.get("p99") or {}
        if p99.get("passed") is not True:
            why.append("the p99 check did not pass (or was not run)")
        inc = p99.get("increase_pct")
        if isinstance(inc, int | float) and inc > rules["max_p99_increase_pct"]:
            why.append(f"p99 goes up {inc:g}%, limit {rules['max_p99_increase_pct']:g}%")
        elif not isinstance(inc, int | float):
            why.append("the p99 check does not say how much p99 changes (increase_pct)")
        pct = m.primary_saving_pct(p)
        if pct is None:
            why.append("no saving stated as a percent")
        elif pct < rules["min_saving_pct"]:
            why.append(f"saving {pct:g}% is under the {rules['min_saving_pct']:g}% minimum")
        ch = p.get("change", {})
        drop = ch.get("drop_pct")
        if "max_cap_drop_pct" in rules and (
                not isinstance(drop, int | float) or drop > rules["max_cap_drop_pct"]):
            why.append("the cap drop is missing or over max_cap_drop_pct")
        tgt = (ch.get("after") or {}).get("target_pct")
        if "max_target_pct" in rules and (
                not isinstance(tgt, int | float) or tgt > rules["max_target_pct"]):
            why.append("the shed target is missing or over max_target_pct")
        if self.max_per_day is not None and approved_today >= self.max_per_day:
            why.append(f"already auto-approved {approved_today} today (max {self.max_per_day})")
        if why:
            return False, why
        return True, ["every threshold held"]


def load(root: str | Path) -> Policy:
    f = Path(root) / "policy.json"
    if not f.is_file():
        return Policy.off()
    return Policy.from_dict(json.loads(f.read_text(encoding="utf-8")))


TEMPLATE = {
    "format": FORMAT,
    "enabled": False,
    "allowed_labels": ["MEASURED"],
    "kinds": {
        "power_cap": {"min_saving_pct": 5, "max_p99_increase_pct": 10, "max_cap_drop_pct": 25},
    },
    "max_per_day": 3,
}
