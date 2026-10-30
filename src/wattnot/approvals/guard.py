"""The guardrail: classify a change, file it, route it, wait, apply, undo.

A failed comparison never becomes a proposal: `submit` with `gates_passed=False`
raises `NotApproved` and stores nothing. A low-risk change is approved on the
spot (the audit trail says `guardrail:low-risk`). A high-risk change waits for
a person, unless the run started with the explicit `--auto-approve` flag, which
is logged as `auto-approve-flag` and is visibly not a person's decision.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from wattnot.approvals import model as m
from wattnot.approvals import risk as rk
from wattnot.approvals import service as svc
from wattnot.approvals.appliers import NvidiaSmiApplier, PowerCapApplier
from wattnot.approvals.gate import NotApproved
from wattnot.approvals.service import Approvals

LOW_RISK_BY = "guardrail:low-risk"
TIMEOUT_BY = "system:timeout"
PENDING_STRICT = "pending-strict"
PENDING_POLICY = "pending-policy"
STRICT_REASON = "proposed by an open-weights planner: needs a person"
_PLANNER_PARTNERS = {"meta": {"id": "meta", "name": "Meta"},
                     "baseten": {"id": "baseten", "name": "Baseten"}}


class Guard:
    def __init__(self, approvals: Approvals, cfg: rk.RiskConfig | None = None, *,
                 auto_approve_by: str | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.approvals = approvals
        self.cfg = cfg or rk.RiskConfig()
        self.auto_approve_by = auto_approve_by
        self.clock = clock

    # ---- filing and routing
    def submit(self, spec: Mapping, *, gates_passed: bool,
               stock_cap_w: float | None = None,
               tdp_w: float | None = None,
               planner: dict | None = None) -> dict:
        """File a proposal and route it. Returns {"proposal", "risk", "route"}.

        `planner` is {"name", "model", "partner"}; it is stored under
        `source.planner` with its autonomy tier, so it is part of the hashed
        content the approver sees. A strict-tier planner may only auto-apply
        LOW risk: its high-risk changes stay pending (route "pending-strict")
        and --auto-approve does not apply to them.
        """
        if not gates_passed:
            raise NotApproved("the gates did not pass; nothing was proposed or stored")
        name = planner.get("name") if isinstance(planner, Mapping) else None
        # No planner claim means the legacy path: it behaves as standard did.
        # A named but unknown planner is strict (see rk.tier_for).
        tier = "standard" if planner is None else rk.tier_for(self.cfg, name)
        risk = rk.classify(spec, self.cfg, stock_cap_w=stock_cap_w, tdp_w=tdp_w)
        strict_high = tier == "strict" and risk.level == "high"
        if strict_high:
            risk = rk.Risk(level=risk.level, needs_approval=True,
                           reasons=tuple([*risk.reasons, STRICT_REASON]),
                           inputs=risk.inputs)
        work = json.loads(json.dumps(dict(spec)))
        if planner is not None:
            src = dict(work.get("source") or {})
            src["planner"] = {"name": planner.get("name"), "model": planner.get("model"),
                              "partner": planner.get("partner"), "tier": tier}
            work["source"] = src
        change = dict(work.get("change") or {})
        change["risk"] = {"level": risk.level, "reasons": list(risk.reasons),
                          "inputs": risk.inputs}
        work["change"] = change
        work["ttl_s"] = self.cfg.approval_timeout_s
        note = (f"pending-strict: proposed by an open-weights planner ({name}), "
                f"needs a person; --auto-approve does not apply") if strict_high else ""
        p = self.approvals.propose(work, auto=False, note=note)
        if risk.level == "low":
            p = self.approvals.approve(p["id"], LOW_RISK_BY, via=svc.AUTO,
                                       note="; ".join(risk.reasons))
            route = "auto-low-risk"
        elif self.auto_approve_by and tier == "standard":
            p = self.approvals.approve(
                p["id"], f"auto-approve-flag:{self.auto_approve_by}",
                via=svc.PRE_APPROVED,
                note="started with --auto-approve; not a person's decision")
            route = "auto-approve-flag"
        elif strict_high:
            route = PENDING_STRICT
        elif self.approvals.policy().enabled:
            # an operator-set policy: the change is filed and stays PENDING (visible in the
            # UI) until `decide_by_policy` checks it against the policy's thresholds
            route = PENDING_POLICY
        else:
            route = "pending"
        return {"proposal": p, "risk": risk, "route": route}

    def decide_by_policy(self, pid: str) -> dict:
        """Approve a pending proposal if the operator-set policy allows it. If any threshold
        fails it stays pending (a person may still decide, or it times out = reject)."""
        return self.approvals.auto_approve(pid)

    def decline_by_policy(self, pid: str) -> dict:
        """After the hold: a change the policy does not allow is rejected now, with its reasons."""
        return self.approvals.decline_by_policy(pid)

    # ---- waiting
    def wait(self, pid: str, timeout_s: float | None = None, poll_s: float = 0.5,
             sleep: Callable[[float], None] = time.sleep) -> str:
        """Wait until the proposal is decided. On timeout it expires: "expired"."""
        timeout = self.cfg.approval_timeout_s if timeout_s is None else timeout_s
        deadline = self.clock() + timeout
        while True:
            p = self.approvals.get(pid)
            if p["status"] != m.PENDING:
                return p["status"]
            if self.clock() >= deadline:
                self.approvals.expire_due()
                p = self.approvals.store.get(pid)
                if p["status"] == m.PENDING:
                    self._expire(pid)
                return "expired"
            sleep(poll_s)

    def _expire(self, pid: str) -> None:
        now = self.clock()
        with self.approvals.store.lock():
            p = self.approvals.store.get(pid)
            if p["status"] != m.PENDING:
                return
            p["status"] = m.EXPIRED
            self.approvals.store.put(p)
            self.approvals.store.audit_append(
                ts=now, proposal_id=pid, event="expired", actor="wattnot",
                via="system", content_sha256=p["content_sha256"],
                detail={"was": m.PENDING, "expired_at": p["expires_at"]})

    # ---- applying and undoing
    def apply(self, pid: str, by: str = "wattnot", via: str = svc.CLI,
              applier=None) -> dict:
        return self.approvals.apply(pid, by=by, via=via, applier=applier)

    def undo(self, pid: str, by: str, via: str = svc.HTTP, note: str = "") -> dict:
        return self.approvals.rollback(pid, by, via=via, note=note)

    def write_addendum(self, pid: str, dest_dir: str | Path,
                       sign_key: str | Path | None = None) -> Path:
        """Write `<dest_dir>/<pid>.undo.json` after an undo. An undo after the
        receipt is signed cannot change the signed file, so the undo lives in
        this sidecar: the receipt it belongs to, the undo op (who, when, how),
        and a sha256 of the doc itself.

        If `sign_key` is given, the doc is signed the same offline way as a
        receipt (Ed25519, no public log). The signer stays null when no key is
        given, when signing raises, or when the signing helper cannot handle
        this shape — the unsigned doc with its sha256 is still written.
        """
        from wattnot.receipt.canon import canonical_sha256

        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        p = self.approvals.store.get(pid)
        doc = {"spec": "wattnot.guardrail-addendum/1",
               "proposal_id": pid,
               "receipt_sha256": (p.get("receipt") or {}).get("sha256"),
               "ops": [o for o in self.ops([pid]) if o["op"] == "undo"],
               "integrity": {"sha256": None, "signer": None}}
        doc["integrity"]["sha256"] = canonical_sha256(doc)
        path = dest / f"{pid}.undo.json"
        path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        if sign_key is not None:
            try:
                from wattnot.receipt.sign import sign as _sign
                _sign(path, no_rekor=True, key_path=sign_key)
            except Exception:  # noqa: BLE001 - best effort; the unsigned doc stands
                pass
        return path

    # ---- the receipt trail
    def ops(self, pids: list[str] | None = None) -> list[dict]:
        """Receipt ops in audit order: approve, reject, apply, undo."""
        keep = set(pids) if pids is not None else None
        out = []
        for e in self.approvals.store.audit_entries():
            pid = e.get("proposal_id")
            if pid is None or (keep is not None and pid not in keep):
                continue
            ev = e.get("event")
            if ev == "approved":
                op = "approve"
            elif ev == "rejected":
                op = "reject"
            elif ev == "expired":
                op = "reject"
            elif ev == "applied":
                op = "apply"
            elif ev in ("rolled_back", "revoked"):
                op = "undo"
            else:
                continue  # proposed, failed, linked, grants: not receipt ops
            row: dict[str, Any] = {
                "op": op, "change_id": pid, "by": e.get("actor"), "via": e.get("via"),
                "at": e.get("ts"), "note": (e.get("detail") or {}).get("note", ""),
            }
            if ev == "expired":
                row["by"] = TIMEOUT_BY
            if op in ("approve", "reject"):
                row["risk"] = self._risk_level(pid)
            prop = self._proposal_of(pid)
            row["planner"] = self._planner_of(prop)
            row["partner"] = self._partner_for(op, row["by"], prop)
            row["proposed_by"] = self._proposed_by(prop)
            out.append(row)
        return out

    def _proposal_of(self, pid: str) -> dict | None:
        try:
            return self.approvals.store.get(pid)
        except Exception:  # noqa: BLE001 - a missing file is not an ops failure
            return None

    def _planner_of(self, prop: dict | None) -> dict | None:
        """The planner that proposed this change, with its tier (None when unknown)."""
        src = (prop or {}).get("source") or {}
        pl = src.get("planner")
        if not isinstance(pl, Mapping):
            return None
        return {"name": pl.get("name"), "model": pl.get("model"),
                "partner": pl.get("partner"), "tier": pl.get("tier")}

    def _proposed_by(self, prop: dict | None) -> dict | None:
        """Who proposed it, as a partner: Meta or Baseten (None when unknown)."""
        pl = self._planner_of(prop)
        if pl is None:
            return None
        return _PLANNER_PARTNERS.get(pl.get("partner"))

    def _partner_for(self, op: str, by: str | None,
                     prop: dict | None) -> dict | None:
        """Who is behind an op. A person's decision is the Operator; an
        automatic one is the Auto policy. An apply/undo on a real card is the
        E2E H100; on the mock it is null."""
        if op in ("approve", "reject"):
            if (by or "").startswith(("dashboard:", "user:")):
                return {"id": "operator", "name": "Operator"}
            return {"id": "policy", "name": "Auto policy"}
        if (prop or {}).get("label") in ("MOCK", "SIMULATED"):
            return None
        applier = self.approvals.appliers.get((prop or {}).get("kind"))
        if isinstance(applier, (NvidiaSmiApplier, PowerCapApplier)):
            return {"id": "e2e", "name": "E2E H100"}
        return None

    def _risk_level(self, pid: str) -> str | None:
        try:
            p = self.approvals.store.get(pid)
        except Exception:  # noqa: BLE001 - a missing file is not an ops failure
            return None
        risk = (p.get("change") or {}).get("risk") or {}
        return risk.get("level")

    def receipt_block(self, pids: list[str] | None = None) -> dict:
        entries = self.approvals.store.audit_entries()
        head = entries[-1]["sha256"] if entries else None
        return {"spec": "wattnot.guardrail-ops/1", "config": self.cfg.to_dict(),
                "auto_approve": bool(self.auto_approve_by),
                "approval_policy": self._policy_block(),
                "ops": self.ops(pids),
                "audit_chain_ok": self.approvals.verify() == [],
                "audit_head_sha256": head}

    def _policy_block(self) -> dict | None:
        try:
            pol = self.approvals.policy()
        except Exception:  # noqa: BLE001 - a broken policy file approves nothing
            return None
        if not pol.enabled:
            return None
        return {**pol.to_dict(), "sha256": pol.sha256(), "approves_as": pol.operator_line()}


__all__ = ["Guard", "NotApproved", "LOW_RISK_BY", "TIMEOUT_BY", "PENDING_STRICT",
           "STRICT_REASON", "PENDING_POLICY"]
