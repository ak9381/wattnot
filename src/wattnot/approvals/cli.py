"""`wattnot proposals ...`: look at what WattNot wants to change, and decide.

    wattnot proposals list [--status pending] [--kind power_cap] [--json]
    wattnot proposals show  <id>
    wattnot proposals approve <id> [--note ...] [--sha256 <hash you reviewed>] [--apply]
    wattnot proposals reject  <id> [--note ...]
    wattnot proposals apply   <id>        approved, now change the hardware (on the GPU host)
    wattnot proposals rollback <id>       undo an applied change (revokes a live trial grant)
    wattnot proposals audit [<id>] / verify
    wattnot proposals expire              mark overdue proposals as expired
    wattnot proposals link-receipt <id> <receipt.json>
    wattnot proposals policy show|init    the optional auto-approve policy (off by default)
    wattnot proposals serve               local JSON API for the frontend

Every command takes `--dir` (default $WATTNOT_PROPOSALS_DIR or ./proposals). An id can be
shortened to any unique prefix.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path

from wattnot.approvals import model as m
from wattnot.approvals import policy as pol
from wattnot.approvals.appliers import ApplyError
from wattnot.approvals.service import CLI, Approvals, StateError
from wattnot.approvals.store import NotFound

HOST_OWNED = (m.POWER_CAP,)  # applying these needs the GPU on this machine


def add_parser(sub) -> None:
    p = sub.add_parser("proposals", help="review, approve, apply and roll back proposed changes",
                       description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", default=None, help="proposals folder "
                   "(default $WATTNOT_PROPOSALS_DIR or ./proposals)")
    ps = p.add_subparsers(dest="prop_cmd")
    ls = ps.add_parser("list", help="list proposals (newest last)")
    ls.add_argument("--status", choices=m.STATUSES)
    ls.add_argument("--kind", choices=m.KINDS)
    ls.add_argument("--json", action="store_true")
    sh = ps.add_parser("show", help="one proposal in full, with its audit trail")
    sh.add_argument("id")
    sh.add_argument("--json", action="store_true")
    ap = ps.add_parser("approve", help="approve a pending proposal (nothing is applied yet)")
    ap.add_argument("id")
    ap.add_argument("--by", help="who is approving (default: you, or $WATTNOT_APPROVER)")
    ap.add_argument("--note", default="")
    ap.add_argument("--sha256", help="the content hash you reviewed; refuses if it changed")
    ap.add_argument("--apply", action="store_true", help="apply it right after approving")
    rj = ps.add_parser("reject", help="reject a pending proposal")
    rj.add_argument("id")
    rj.add_argument("--by")
    rj.add_argument("--note", default="")
    ay = ps.add_parser("apply", help="apply an approved proposal on this machine")
    ay.add_argument("id")
    ay.add_argument("--by")
    rb = ps.add_parser("rollback", help="undo an applied proposal")
    rb.add_argument("id")
    rb.add_argument("--by")
    rb.add_argument("--note", default="")
    au = ps.add_parser("audit", help="the audit trail, all of it or for one proposal")
    au.add_argument("id", nargs="?")
    au.add_argument("--json", action="store_true")
    ps.add_parser("verify", help="check the audit trail hash chain end to end")
    ps.add_parser("expire", help="mark overdue proposals as expired")
    lr = ps.add_parser("link-receipt", help="link the receipt that proves a saving")
    lr.add_argument("id")
    lr.add_argument("receipt")
    po = ps.add_parser("policy", help="the optional auto-approve policy")
    po.add_argument("policy_cmd", choices=("show", "init", "set"))
    po.add_argument("--by", help="set: the operator authorising the policy (required)")
    po.add_argument("--min-saving-pct", type=float, default=3.0)
    po.add_argument("--max-p99-increase-pct", type=float, default=10.0)
    po.add_argument("--max-cap-drop-pct", type=float, default=45.0)
    po.add_argument("--max-per-day", type=int, default=3)
    po.add_argument("--file", type=Path, default=None,
                    help="set: write here instead of <dir>/policy.json")
    sv = ps.add_parser("serve", help="local JSON API (127.0.0.1) for the frontend")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8781)


# ---- helpers

def _policy_set(a, f: Path) -> int:
    """Write an ENABLED power-cap policy signed off by a named operator, stamped now (UTC).
    Used for an unattended run: every approval it makes says who set it and when."""
    from datetime import UTC, datetime
    if not (a.by or "").strip():
        print("policy set needs --by <operator name>", file=sys.stderr)
        return 2
    if f.exists():
        print(f"{f} already exists; not touching it", file=sys.stderr)
        return 1
    d = {"format": pol.FORMAT, "enabled": True, "allowed_labels": ["MEASURED"],
         "kinds": {m.POWER_CAP: {"min_saving_pct": a.min_saving_pct,
                                 "max_p99_increase_pct": a.max_p99_increase_pct,
                                 "max_cap_drop_pct": a.max_cap_drop_pct}},
         "max_per_day": a.max_per_day, "set_by": a.by.strip(),
         "set_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    p = pol.Policy.from_dict(d)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(p.to_dict(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {f}: {p.operator_line()} (sha256 {p.sha256()[:12]})")
    return 0


def _resolve(a: Approvals, pid: str) -> str:
    if a.store.exists(pid):
        return pid
    hits = [p["id"] for p in a.store.all() if p["id"].startswith(pid)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise NotFound(f"no proposal matches {pid!r}")
    raise StateError(f"{pid!r} matches {len(hits)} proposals; use more characters")


def _unit(s: Mapping) -> str:
    return f"{s['value']}{'' if s.get('unit') == '%' else ' '}{s.get('unit', '')}".strip()


def _line(p: Mapping) -> str:
    sv = (p.get("savings") or [{}])[0]
    pct = f"{sv['value']:.1f}%" if sv.get("unit") == "%" and sv.get("value") is not None else "-"
    return (f"{p['id']}  {p['status']:<11} {p['kind']:<12} {p['label']:<9} {pct:>7}  "
            f"{p['title'][:60]}")


def format_proposal(p: Mapping, audit: list[dict] | None = None) -> str:
    out = [f"{p['title']}", f"  id        {p['id']}", f"  status    {p['status'].upper()}",
           f"  kind      {p['kind']} ({p['scope']})", f"  label     {p['label']}",
           f"  expires   {p['expires_at']}", f"  hash      {p['content_sha256']}", "",
           p["summary"], "", "What changes:"]
    ch = p["change"]
    if "before" in ch and "after" in ch:
        keys = ch.get("changed") or sorted(set(ch["before"]) | set(ch["after"]))
        for k in keys:
            out.append(f"  {k}: {ch['before'].get(k)} -> {ch['after'].get(k)}")
        if ch.get("target"):
            out.append(f"  target: {ch['target']}")
    else:
        out.append("  " + json.dumps(ch, sort_keys=True))
    out += ["", "Savings:"]
    out += [f"  {s['metric']}: {_unit(s)}  [{s['label']}]"
            + (f"  ({s['low']} to {s['high']})" if s.get("low") is not None else "")
            for s in p["savings"]] or ["  none claimed"]
    out += ["", "Checks:"]
    for name, c in (p.get("checks") or {}).items():
        flag = {True: "PASS", False: "FAIL", None: "n/a"}.get(c.get("passed"), "n/a")
        out.append(f"  {name:<8} {flag}  {c.get('detail', '')}")
    if not p.get("checks"):
        out.append("  none recorded (auto-approve will not touch this)")
    pv = p.get("receipt_preview") or {}
    out += ["", "Receipt preview:"]
    if pv.get("profile"):
        out.append(f"  profile {pv['profile']}, label {pv.get('label')}, "
                   f"expected grade {pv.get('expected_grade')}")
    elif not pv:
        out.append("  none recorded")
    out += [f"  will claim: {x}" for x in pv.get("will_claim", [])]
    out += [f"  not claimed: {x}" for x in pv.get("not_claimed", [])]
    ev = pv.get("evidence_receipt")
    if ev:
        out.append(f"  evidence: {ev.get('receipt_id')} sha256 {str(ev.get('sha256'))[:16]}...")
    for field, label in (("decision", "decision"), ("applied", "applied"),
                         ("rolled_back", "rolled back"), ("receipt", "linked receipt")):
        v = p.get(field)
        if v:
            out += ["", f"{label}: " + ", ".join(f"{k}={v[k]}" for k in v
                                                  if k != "usage" and v[k] is not None)]
    if audit:
        out += ["", "Audit trail:"]
        out += [f"  #{e['seq']} {e['ts']} {e['event']:<14} {e['actor']} via {e['via']}"
                for e in audit]
    return "\n".join(out)


def _by(a) -> str:
    return getattr(a, "by", None) or m.default_actor()


def _open(a) -> Approvals:
    apps = Approvals(a.dir)
    return apps


def _with_hardware(apps: Approvals, p: Mapping) -> None:
    """Power caps are written on the GPU in this machine, so open NVML for them."""
    if p["kind"] in HOST_OWNED:
        from wattnot.approvals.appliers import nvml_appliers
        from wattnot.probe import open_nvml

        try:
            apps.appliers.update(nvml_appliers(open_nvml()))
        except Exception as e:  # noqa: BLE001
            raise StateError(f"cannot reach the GPU from here ({e}). Run this on the GPU "
                             "host, or approve now and apply there.") from e


# ---- dispatch

def dispatch(a) -> int:
    apps = _open(a)
    cmd = a.prop_cmd
    try:
        if cmd in (None, "list"):
            items = apps.list(getattr(a, "status", None), getattr(a, "kind", None))
            if getattr(a, "json", False):
                print(json.dumps(items, indent=2))
            elif not items:
                print(f"no proposals in {apps.store.root}")
            else:
                print("\n".join(_line(p) for p in items))
            return 0
        if cmd == "audit":
            pid = _resolve(apps, a.id) if a.id else None
            es = apps.audit(pid)
            if a.json:
                print(json.dumps(es, indent=2))
            else:
                print("\n".join(f"#{e['seq']} {e['ts']} {e['event']:<14} "
                                f"{e['proposal_id'] or '-'} {e['actor']} via {e['via']}"
                                for e in es) or "audit trail is empty")
            return 0
        if cmd == "verify":
            problems = apps.verify()
            n = len(apps.audit())
            if problems:
                print(f"AUDIT TRAIL PROBLEMS ({len(problems)}):\n  " + "\n  ".join(problems))
                return 1
            print(f"audit trail ok: {n} entries, hash chain intact")
            return 0
        if cmd == "expire":
            ids = apps.expire_due()
            print("\n".join(f"expired {i}" for i in ids) or "nothing to expire")
            return 0
        if cmd == "policy":
            f = Path(apps.store.root) / "policy.json"
            if a.policy_cmd == "set":
                return _policy_set(a, Path(a.file) if a.file else f)
            if a.policy_cmd == "init":
                if f.exists():
                    print(f"{f} already exists; not touching it")
                    return 1
                apps.store.root.mkdir(parents=True, exist_ok=True)
                f.write_text(json.dumps(pol.TEMPLATE, indent=2) + "\n", encoding="utf-8")
                print(f"wrote {f} with auto-approve OFF. Edit the thresholds and set "
                      '"enabled": true to turn it on.')
                return 0
            p = apps.policy()
            print(json.dumps({**p.to_dict(), "sha256": p.sha256(),
                              "file": str(f) if f.exists() else None}, indent=2))
            return 0
        if cmd == "serve":
            from wattnot.approvals.server import serve

            return serve(apps, host=a.host, port=a.port)
        pid = _resolve(apps, a.id)
        if cmd == "show":
            p = apps.get(pid)
            print(json.dumps({**p, "audit": apps.audit(pid)}, indent=2) if a.json
                  else format_proposal(p, apps.audit(pid)))
            return 0
        if cmd == "approve":
            p = apps.approve(pid, _by(a), via=CLI, note=a.note, expect_sha256=a.sha256)
            print(f"approved {pid} by {p['decision']['by']} (hash {p['content_sha256'][:12]})")
            if not a.apply:
                print(f"nothing is applied yet. Apply with: wattnot proposals apply {pid}")
                return 0
            cmd = "apply"
        if cmd == "reject":
            apps.reject(pid, _by(a), via=CLI, note=a.note)
            print(f"rejected {pid}; nothing was changed")
            return 0
        if cmd == "apply":
            _with_hardware(apps, apps.store.get(pid))
            p = apps.apply(pid, by=_by(a), via=CLI)
            print(f"applied {pid}; to undo: wattnot proposals rollback {pid}")
            if p.get("receipt"):
                print(f"linked receipt {p['receipt']['receipt_id']}")
            return 0
        if cmd == "rollback":
            _with_hardware(apps, apps.store.get(pid))
            apps.rollback(pid, _by(a), via=CLI, note=a.note)
            print(f"rolled back {pid}")
            return 0
        if cmd == "link-receipt":
            p = apps.link_receipt(pid, a.receipt, by=_by(a))
            print(f"linked {p['receipt']['receipt_id']} (sha256 {p['receipt']['sha256'][:16]}...)")
            return 0
    except (NotFound, StateError, ApplyError, m.ProposalError, pol.PolicyError, OSError,
            ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print("use: wattnot proposals list|show|approve|reject|apply|rollback|audit|verify|"
          "expire|link-receipt|policy|serve", file=sys.stderr)
    return 2
