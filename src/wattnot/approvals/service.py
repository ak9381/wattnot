"""The approval service: propose, decide, apply, roll back. Everything is written to the
store and the audit trail, under one lock.

Rules that are enforced here, not left to callers:

* Nothing is applied unless the proposal is `approved`, not expired, and its reviewed
  content still matches the hash that was approved.
* A decision is final: approve, reject and expire cannot be undone (propose again instead).
* Every state change adds one entry to the hash-chained audit log, with who and how.
* Auto-approve only happens when a human has enabled a policy file (see `policy.py`).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from pathlib import Path

from wattnot.approvals import model as m
from wattnot.approvals import policy as pol
from wattnot.approvals.appliers import Applier, ApplyError, standard
from wattnot.approvals.store import NotFound, Store

# via: how the decision reached us
CLI, HTTP, AUTO, PRE_APPROVED, DRY_RUN = "cli", "http", "auto-policy", "pre-approved", "dry-run"


class StateError(RuntimeError):
    """The proposal is not in a state that allows this."""


class Approvals:
    def __init__(self, root: str | Path | None = None, *,
                 appliers: Mapping[str, Applier] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.store = Store(root)
        self.clock = clock
        self.appliers: dict[str, Applier] = {**standard(self.store), **(appliers or {})}

    # ---- reading
    def policy(self) -> pol.Policy:
        return pol.load(self.store.root)

    def get(self, pid: str) -> dict:
        self.expire_due()
        return self.store.get(pid)

    def list(self, status: str | None = None, kind: str | None = None) -> list[dict]:
        self.expire_due()
        return [p for p in self.store.all()
                if (status is None or p["status"] == status)
                and (kind is None or p["kind"] == kind)]

    def audit(self, pid: str | None = None) -> list[dict]:
        return self.store.audit_for(pid) if pid else self.store.audit_entries()

    def verify(self) -> list[str]:
        return self.store.verify_chain()

    # ---- creating
    def propose(self, spec: Mapping, *, auto: bool = True, actor: str = "wattnot",
                note: str = "") -> dict:
        """Store a new pending proposal. If a human enabled a policy and every threshold
        holds, it is approved on the spot (the audit trail says so)."""
        now = self.clock()
        p = m.build(spec, ts=now, created_by=spec.get("created_by", actor))
        detail = {"kind": p["kind"], "scope": p["scope"],
                  "title": p["title"], "label": p["label"]}
        if note:
            detail["note"] = note
        with self.store.lock():
            self.store.put(p)
            self.store.audit_append(ts=now, proposal_id=p["id"], event="proposed", actor=actor,
                                    via="system", content_sha256=p["content_sha256"],
                                    detail=detail)
        if auto:
            return self.auto_approve(p["id"])
        return p

    def auto_approve(self, pid: str) -> dict:
        """Approve `pid` if the enabled policy allows it; otherwise leave it as it is."""
        policy = self.policy()
        with self.store.lock():
            p = self.store.get(pid)
            if p["status"] != m.PENDING or not policy.enabled:
                return p
            today = self._auto_approved_since(self.clock() - 24 * 3600)
            ok, why = policy.evaluate(p, approved_today=today)
            if not ok:
                return p
            note = "; ".join(why)
            extra = {"policy_sha256": policy.sha256()}
            if policy.set_by:
                note = f"{policy.operator_line()}; {note}"
                extra.update(policy_set_by=policy.set_by, policy_set_at=policy.set_at)
            return self._decide(p, m.APPROVED, by=f"auto-policy:{policy.sha256()[:12]}",
                                via=AUTO, note=note, extra=extra)

    def _auto_approved_since(self, since: float) -> int:
        return sum(1 for e in self.store.audit_entries()
                   if e["event"] == "approved" and e["via"] == AUTO
                   and m.parse_iso(e["ts"]) >= since)

    # ---- deciding
    def approve(self, pid: str, by: str, *, via: str = CLI, note: str = "",
                expect_sha256: str | None = None) -> dict:
        """Record a human approval. `expect_sha256` (a prefix is fine) is the hash the person
        was shown; if the proposal's content differs, nothing is approved."""
        self.expire_due()
        with self.store.lock():
            p = self.store.get(pid)
            self._need(p, m.PENDING, "approve")
            if expect_sha256 and not p["content_sha256"].startswith(expect_sha256.lower()):
                raise StateError("the proposal changed since you looked at it "
                                 f"(hash is now {p['content_sha256'][:12]}); review it again")
            return self._decide(p, m.APPROVED, by=by, via=via, note=note)

    def reject(self, pid: str, by: str, *, via: str = CLI, note: str = "") -> dict:
        self.expire_due()
        with self.store.lock():
            p = self.store.get(pid)
            self._need(p, m.PENDING, "reject")
            return self._decide(p, m.REJECTED, by=by, via=via, note=note)

    def _decide(self, p: dict, status: str, *, by: str, via: str, note: str,
                extra: dict | None = None) -> dict:
        now = self.clock()
        p["status"] = status
        p["decision"] = {"by": by, "at": m.iso(now), "via": via, "note": note,
                         "content_sha256": p["content_sha256"], **(extra or {})}
        self.store.put(p)
        self.store.audit_append(
            ts=now, proposal_id=p["id"], event="approved" if status == m.APPROVED else "rejected",
            actor=by, via=via, content_sha256=p["content_sha256"],
            detail={"note": note, **(extra or {})})
        return p

    # ---- expiry
    def expire_due(self) -> list[str]:
        now = self.clock()
        out: list[str] = []
        due = [p for p in self.store.all()
               if p["status"] in (m.PENDING, m.APPROVED) and m.parse_iso(p["expires_at"]) <= now]
        if not due:
            return out
        with self.store.lock():
            for p in due:
                p = self.store.get(p["id"])
                if p["status"] not in (m.PENDING, m.APPROVED):
                    continue
                was = p["status"]
                p["status"] = m.EXPIRED
                self.store.put(p)
                self.store.audit_append(
                    ts=now, proposal_id=p["id"], event="expired", actor="wattnot", via="system",
                    content_sha256=p["content_sha256"],
                    detail={"was": was, "expired_at": p["expires_at"]})
                out.append(p["id"])
        return out

    # ---- applying
    def apply(self, pid: str, *, by: str = "wattnot", via: str = CLI,
              applier: Applier | None = None) -> dict:
        """Apply an approved proposal. Fails (and changes nothing) in any other state."""
        self.expire_due()
        with self.store.lock():
            p = self.store.get(pid)
            self._need(p, m.APPROVED, "apply")
            if p["scope"] == m.TRIAL:
                raise StateError("a trial grant is opened by a session, not applied")
            if m.content_hash(p) != p["content_sha256"] or \
                    p["decision"]["content_sha256"] != p["content_sha256"]:
                raise StateError("the proposal's content no longer matches what was approved")
            ap = applier or self.appliers.get(p["kind"])
            if ap is None:
                raise StateError(f"no applier for {p['kind']} in this process. Apply it from "
                                 "the session that owns the hardware, or on the GPU host")
            now = self.clock()
            try:
                token = ap.apply(p)
            except Exception as e:  # noqa: BLE001 - whatever went wrong, record it
                p["status"] = m.FAILED
                p["applied"] = {"at": m.iso(now), "by": by,
                                "error": f"{type(e).__name__}: {e}"[:500]}
                self.store.put(p)
                self.store.audit_append(ts=now, proposal_id=p["id"], event="apply_failed",
                                        actor=by, via=via, content_sha256=p["content_sha256"],
                                        detail={"error": p["applied"]["error"]})
                if isinstance(e, ApplyError):
                    raise
                raise ApplyError(f"could not apply {p['id']}: {e}") from e
            p["status"] = m.APPLIED
            p["applied"] = {"at": m.iso(now), "by": by, "via": via,
                            "approved_by": p["decision"]["by"], "token": token}
            ev = (p.get("receipt_preview") or {}).get("evidence_receipt") or {}
            if ev.get("sha256") and not p.get("receipt"):
                # The receipt that backs the saving already exists: link it at apply time.
                p["receipt"] = {"receipt_id": ev.get("receipt_id"), "sha256": ev["sha256"],
                                "path": None, "grade": ev.get("grade"),
                                "signed": bool(ev.get("signed")), "linked_at": m.iso(now),
                                "kind": "evidence"}
            self.store.put(p)
            self.store.audit_append(
                ts=now, proposal_id=p["id"], event="applied", actor=by, via=via,
                content_sha256=p["content_sha256"],
                detail={"approved_by": p["decision"]["by"], "token": token,
                        "receipt_sha256": (p.get("receipt") or {}).get("sha256")})
            return p

    def rollback(self, pid: str, by: str, *, via: str = CLI, note: str = "",
                 applier: Applier | None = None) -> dict:
        """Undo an applied change. For a live trial grant this revokes it: the session
        that holds it stops at its next change and restores the hardware."""
        with self.store.lock():
            p = self.store.get(pid)
            self._need(p, m.APPLIED, "roll back")
            now = self.clock()
            if p["scope"] == m.TRIAL:
                p["status"] = m.ROLLED_BACK
                p["rolled_back"] = {"at": m.iso(now), "by": by, "via": via, "note": note,
                                    "revoked": True}
                self.store.put(p)
                self.store.audit_append(ts=now, proposal_id=p["id"], event="revoked", actor=by,
                                        via=via, content_sha256=p["content_sha256"],
                                        detail={"note": note})
                return p
            ap = applier or self.appliers.get(p["kind"])
            if ap is None:
                raise StateError(f"no applier for {p['kind']} in this process; roll back from "
                                 "the host that owns the hardware")
            ap.rollback(p["applied"]["token"], p)
            p["status"] = m.ROLLED_BACK
            p["rolled_back"] = {"at": m.iso(now), "by": by, "via": via, "note": note}
            self.store.put(p)
            self.store.audit_append(ts=now, proposal_id=p["id"], event="rolled_back", actor=by,
                                    via=via, content_sha256=p["content_sha256"],
                                    detail={"note": note})
            return p

    # ---- trial grants (permission to try changes inside bounds, then restore)
    def open_grant(self, pid: str, *, by: str, via: str) -> dict:
        """Mark an approved trial grant as in use. Called by the session that holds it."""
        self.expire_due()
        with self.store.lock():
            p = self.store.get(pid)
            self._need(p, m.APPROVED, "open")
            if p["scope"] != m.TRIAL:
                raise StateError("only a trial_grant is opened this way")
            now = self.clock()
            p["status"] = m.APPLIED
            p["applied"] = {"at": m.iso(now), "by": by, "via": via,
                            "approved_by": p["decision"]["by"], "usage": _empty_usage()}
            self.store.put(p)
            self.store.audit_append(ts=now, proposal_id=p["id"], event="grant_opened", actor=by,
                                    via=via, content_sha256=p["content_sha256"],
                                    detail={"approved_by": p["decision"]["by"]})
            return p

    def close_grant(self, pid: str, usage: Mapping, *, by: str = "wattnot") -> dict:
        """End a grant and record what it was used for. A grant that was revoked or has
        expired keeps that status; the usage is still recorded."""
        with self.store.lock():
            p = self.store.get(pid)
            now = self.clock()
            if p.get("applied") is not None:
                p["applied"]["usage"] = dict(usage)
            if p["status"] == m.APPLIED:
                p["status"] = m.CLOSED
            self.store.put(p)
            self.store.audit_append(ts=now, proposal_id=p["id"], event="grant_closed", actor=by,
                                    via="system", content_sha256=p["content_sha256"],
                                    detail={"status_at_close": p["status"], "usage": dict(usage)})
            return p

    def save_usage(self, pid: str, usage: Mapping) -> None:
        """Update the usage counters on a live grant (no audit entry; close writes one)."""
        with self.store.lock():
            p = self.store.get(pid)
            if p.get("applied") is not None and p["status"] == m.APPLIED:
                p["applied"]["usage"] = dict(usage)
                self.store.put(p)

    # ---- receipts
    def link_receipt(self, pid: str, receipt: Mapping | str | Path, *, by: str = "wattnot") -> dict:
        """Tie the proposal to the receipt that proves the saving: its id, its sha256 (from
        the receipt's own integrity block) and whether it is signed. Re-run with the same
        receipt is a no-op."""
        import json

        path = None
        if not isinstance(receipt, Mapping):
            path = Path(receipt)
            receipt = json.loads(path.read_text(encoding="utf-8"))
        integ = receipt.get("integrity") or {}
        rid = receipt.get("receipt_id")
        sha = integ.get("sha256")
        if not rid or not sha:
            raise StateError("that is not a WattNot receipt (no receipt_id or integrity.sha256)")
        link = {"receipt_id": rid, "sha256": sha, "path": str(path) if path else None,
                "grade": receipt.get("grade"), "signed": bool(integ.get("signer")),
                "linked_at": m.iso(self.clock())}
        with self.store.lock():
            p = self.store.get(pid)
            if p["status"] in (m.PENDING, m.REJECTED, m.EXPIRED):
                raise StateError(f"cannot link a receipt to a {p['status']} proposal")
            if p.get("receipt") and p["receipt"]["sha256"] == sha:
                return p
            p["receipt"] = link
            self.store.put(p)
            self.store.audit_append(ts=self.clock(), proposal_id=p["id"], event="receipt_linked",
                                    actor=by, via="system", content_sha256=p["content_sha256"],
                                    detail={"receipt_id": rid, "receipt_sha256": sha,
                                            "signed": link["signed"]})
            return p

    def receipt_block(self, pid: str) -> dict:
        """The block a receipt can carry to point back at the approval."""
        p = self.get(pid)
        d = p.get("decision") or {}
        return {"proposal_id": p["id"], "proposal_sha256": p["content_sha256"],
                "approved_by": d.get("by"), "approved_at": d.get("at"), "via": d.get("via")}

    # ---- helpers
    @staticmethod
    def _need(p: Mapping, status: str, verb: str) -> None:
        if p["status"] != status:
            raise StateError(f"cannot {verb} {p['id']}: it is {p['status']}, not {status}")


def _empty_usage() -> dict:
    return {"changes": 0, "by_kind": {}}


__all__ = ["Approvals", "StateError", "NotFound", "CLI", "HTTP", "AUTO", "PRE_APPROVED",
           "DRY_RUN"]
