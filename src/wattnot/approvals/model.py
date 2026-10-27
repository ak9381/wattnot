"""The proposal record: what WattNot wants to change, why, and what a human decided.

A proposal is plain JSON. The part a human reviews (kind, change, savings, checks, receipt
preview, expiry) is hashed into `content_sha256` when the proposal is created and never
edited. Everything that happens later (approved, applied, rolled back, receipt linked) goes
in separate fields and in the audit log, so the thing someone approved can always be shown
again byte for byte.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

FORMAT = "wattnot.proposal/1"

# What can be proposed.
POWER_CAP = "power_cap"          # a GPU power limit
VLLM_CONFIG = "vllm_config"      # vLLM batch / KV cache / quantization settings
CLOUD_POLICY = "cloud_policy"    # hosted-model effort and cache routing policy
FLEX_SHED = "flex_shed"          # cut GPU draw for a grid event
MOE_ROUTING = "moe_routing"      # MoE routing knob: adaptive top-k (off unless approved)
TRIAL_GRANT = "trial_grant"      # permission to try changes inside bounds, to take measurements
KINDS = (POWER_CAP, VLLM_CONFIG, CLOUD_POLICY, FLEX_SHED, MOE_ROUTING, TRIAL_GRANT)

# Commit = leave the change in place. Trial = change things temporarily to measure, then restore.
COMMIT, TRIAL = "commit", "trial"
SCOPES = (COMMIT, TRIAL)

# Lifecycle.
PENDING, APPROVED, REJECTED, EXPIRED = "pending", "approved", "rejected", "expired"
APPLIED, FAILED, ROLLED_BACK, CLOSED = "applied", "failed", "rolled_back", "closed"
STATUSES = (PENDING, APPROVED, REJECTED, EXPIRED, APPLIED, FAILED, ROLLED_BACK, CLOSED)
TERMINAL = (REJECTED, EXPIRED, FAILED, ROLLED_BACK, CLOSED)

LABELS = ("MEASURED", "ESTIMATE", "SIMULATED", "MOCK")
# Least trustworthy first. A proposal's overall label is the worst label on any saving.
_WORST_FIRST = ("MOCK", "SIMULATED", "ESTIMATE", "MEASURED")

DEFAULT_TTL_S = 24 * 3600
# The fields that make up what a human approves. Nothing else is part of the hash.
HASHED = ("format", "id", "kind", "scope", "title", "summary", "created_at", "expires_at",
          "created_by", "source", "label", "change", "savings", "checks", "receipt_preview")


def now_utc(ts: float | None = None) -> datetime:
    if ts is None:
        return datetime.now(UTC)
    return datetime.fromtimestamp(ts, UTC)


def iso(ts: float) -> str:
    return now_utc(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> float:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()


def canon(obj: Any) -> str:
    """Stable JSON used for hashing (sorted keys, no spaces)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(p: Mapping) -> str:
    return sha256_hex(canon({k: p.get(k) for k in HASHED}))


def new_id(ts: float) -> str:
    return f"prop_{now_utc(ts).strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"


def worst_label(labels: list[str]) -> str:
    """The least trustworthy label in the list (MOCK < SIMULATED < ESTIMATE < MEASURED)."""
    for lab in _WORST_FIRST:
        if lab in labels:
            return lab
    return "ESTIMATE"


def default_actor() -> str:
    import getpass
    try:
        who = os.environ.get("WATTNOT_APPROVER") or getpass.getuser()
    except Exception:  # noqa: BLE001 - no passwd entry in some containers
        who = "unknown"
    return f"user:{who}"


class ProposalError(ValueError):
    """The proposal, or the request about it, is not valid."""


def validate_spec(spec: Mapping) -> None:
    """Check a new proposal before it is stored. Raises ProposalError."""
    kind = spec.get("kind")
    if kind not in KINDS:
        raise ProposalError(f"kind must be one of {', '.join(KINDS)}")
    scope = spec.get("scope", TRIAL if kind == TRIAL_GRANT else COMMIT)
    if scope not in SCOPES:
        raise ProposalError(f"scope must be one of {', '.join(SCOPES)}")
    if (kind == TRIAL_GRANT) != (scope == TRIAL):
        raise ProposalError("trial_grant proposals have scope 'trial', and only they do")
    for key in ("title", "summary"):
        if not str(spec.get(key) or "").strip():
            raise ProposalError(f"{key} is required")
    change = spec.get("change")
    if not isinstance(change, Mapping) or not change:
        raise ProposalError("change is required (what changes, from and to)")
    if kind != TRIAL_GRANT and "after" not in change:
        raise ProposalError("change needs an 'after' (and a 'before' when it is known)")
    if kind == TRIAL_GRANT and not isinstance(change.get("bounds"), Mapping):
        raise ProposalError("a trial_grant change needs 'bounds'")
    for s in spec.get("savings", []):
        if not isinstance(s, Mapping) or "metric" not in s or "value" not in s:
            raise ProposalError("each saving needs a metric and a value")
        if s.get("label") not in LABELS:
            raise ProposalError(f"each saving needs a label from {', '.join(LABELS)}")
    checks = spec.get("checks", {})
    for name in ("quality", "p99"):
        c = checks.get(name)
        if c is not None and (not isinstance(c, Mapping) or "passed" not in c):
            raise ProposalError(f"checks.{name} needs a 'passed' field (true, false or null)")
    ttl = spec.get("ttl_s")
    if ttl is not None and not (isinstance(ttl, int | float) and ttl > 0):
        raise ProposalError("ttl_s must be a positive number of seconds")


def build(spec: Mapping, *, ts: float, created_by: str = "wattnot") -> dict:
    """A stored proposal (status pending) from a spec. The spec is not modified."""
    validate_spec(spec)
    kind = spec["kind"]
    scope = spec.get("scope", TRIAL if kind == TRIAL_GRANT else COMMIT)
    ttl = float(spec.get("ttl_s") or DEFAULT_TTL_S)
    savings = [dict(s) for s in spec.get("savings", [])]
    checks = {k: dict(v) for k, v in (spec.get("checks") or {}).items() if v is not None}
    # A trial grant promises no saving, so it has no label ("NONE").
    label = spec.get("label") or (worst_label([s["label"] for s in savings])
                                  if savings else "NONE")
    p = {
        "format": FORMAT,
        "id": spec.get("id") or new_id(ts),
        "kind": kind,
        "scope": scope,
        "title": str(spec["title"]).strip(),
        "summary": str(spec["summary"]).strip(),
        "created_at": iso(ts),
        "expires_at": iso(ts + ttl),
        "created_by": spec.get("created_by", created_by),
        "source": dict(spec.get("source") or {}),
        "label": label,
        "change": json.loads(json.dumps(spec["change"])),
        "savings": savings,
        "checks": checks,
        "receipt_preview": dict(spec.get("receipt_preview") or {}),
    }
    p["content_sha256"] = content_hash(p)
    p.update({"status": PENDING, "decision": None, "applied": None, "rolled_back": None,
              "receipt": None, "policy": None})
    return p


def public_view(p: Mapping) -> dict:
    """What the API and CLI show. The same record, nothing hidden."""
    return dict(p)


def primary_saving_pct(p: Mapping) -> float | None:
    """The first saving whose unit is a percent, used by the auto-approve thresholds."""
    for s in p.get("savings", []):
        if s.get("unit") == "%" and isinstance(s.get("value"), int | float):
            return float(s["value"])
    return None
