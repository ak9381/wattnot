"""The gate: how running code asks permission before it changes anything.

Two ways to ask, because tuning has two kinds of change:

* `gate.request(spec)`     a change to leave in place (a cap, a vLLM config, a routing
                           policy). Proposes it, waits for a decision, then applies it.
* `gate.session(...)`      permission to *try* changes inside bounds so a tuner can take
                           measurements (for example: caps between 400 and 700 W on GPU 0 for
                           two hours). Everything is put back when the session ends. Each
                           change the tuner makes is checked against the bounds and counted.

Modes, set by the caller and never guessed:

    ask           a human decides (CLI, HTTP, or a policy a human enabled). The default.
    pre_approved  the person started the run with the explicit pre-approved flag. It is
                  recorded as approved by "pre-approved:<name>" and never by a person's name.
    dry_run       a simulator with nothing real behind it. Only SIMULATED or MOCK proposals,
                  and a real GPU rig refuses a dry-run gate.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from wattnot.approvals import model as m
from wattnot.approvals import service as svc
from wattnot.approvals.service import Approvals

ASK, PRE_APPROVED, DRY_RUN = "ask", "pre_approved", "dry_run"
MODES = (ASK, PRE_APPROVED, DRY_RUN)
FLUSH_EVERY = 25  # changes between saving the usage counters
RECHECK_S = 1.0   # how often a live session re-reads its grant (so a revoke bites quickly)


class NotApproved(RuntimeError):
    """The change was not approved, was revoked, expired, or is outside what was approved."""


def _bounds_text(b: Mapping) -> str:
    bits = []
    if "power_cap_w" in b:
        c = b["power_cap_w"]
        bits.append(f"power cap {c['min']}-{c['max']} W on GPU {b.get('gpus', [0])}")
    if b.get("vllm_config"):
        bits.append("restart vLLM with different batch / KV cache / quantization settings")
    if "flex_shed" in b:
        f = b["flex_shed"]
        bits.append(f"shed up to {f['max_target_pct']}% for up to {f['max_duration_s']} s")
    if "moe_routing" in b:
        r = b["moe_routing"]
        bits.append(f"run a MoE model with adaptive top-k (router mass {r['min_mass']:g}-"
                    f"{r['max_mass']:g}) next to the normal top-k, in a separate process")
    return "; ".join(bits) or "nothing"


def grant_spec(*, title: str, bounds: Mapping, source: Mapping, ttl_s: float,
               reason: str = "") -> dict:
    """A trial_grant proposal spec. `bounds` keys: power_cap_w {min,max}, gpus [..],
    vllm_config true, flex_shed {max_target_pct, max_duration_s},
    moe_routing {min_mass, max_mass}."""
    return {
        "kind": m.TRIAL_GRANT, "scope": m.TRIAL, "title": title,
        "summary": (f"Try these changes to take measurements: {_bounds_text(bounds)}. "
                    "Every change is put back when the session ends. "
                    + (reason or "")).strip(),
        "ttl_s": ttl_s, "source": dict(source),
        "change": {"target": "session", "bounds": json_clone(bounds),
                   "restores_on_exit": True},
        "receipt_preview": {"note": "Measurements taken under this grant go into the receipts "
                                    "of the runs that use it; nothing here is a saving claim."},
    }


def narrow_bounds(bounds: Mapping, fleet: Any) -> dict:
    """Cut a trial grant's bounds to what the rack photo audit supports.

    Raises NotApproved when no requested GPU has a unit in its photo, or when the cap range
    would be empty. What was cut is written into `bounds["physical"]` so a reviewer sees it.
    """

    out = json_clone(bounds)
    asked = list(out.get("gpus", [0]))
    keep = [g for g in asked if fleet.limit_for(g).eligible]
    dropped = {g: fleet.limit_for(g).reason for g in asked if g not in keep}
    if not keep:
        raise NotApproved("physical check failed: " + "; ".join(dropped.values()))
    out["gpus"] = keep
    note: dict = {"source": "sam-3.1 photo audit", "dropped_gpus": dropped}
    if "power_cap_w" in out:
        ceiling = min(fleet.limit_for(g).ceiling_w for g in keep)
        c = out["power_cap_w"]
        if ceiling < c["min"]:
            raise NotApproved(f"physical check failed: the rack ceiling of {ceiling} W is below "
                              f"the requested minimum cap of {c['min']} W")
        note["ceiling_w"] = ceiling
        if c["max"] > ceiling:
            note["max_cut_from_w"] = c["max"]
            c["max"] = ceiling
    out["physical"] = note
    return out


def json_clone(o: Any) -> Any:
    import json
    return json.loads(json.dumps(o))


class ApprovalGate:
    def __init__(self, approvals: Approvals, *, mode: str = ASK, name: str = "",
                 wait_s: float = 600.0, poll_s: float = 2.0,
                 sleep: Callable[[float], None] = time.sleep,
                 announce: Callable[[str], None] | None = None) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        if mode == PRE_APPROVED and not name.strip():
            raise ValueError("pre_approved needs a name for the audit trail (who or what ran it)")
        self.approvals, self.mode, self.name = approvals, mode, name.strip()
        self.wait_s, self.poll_s, self.sleep = wait_s, poll_s, sleep
        self.announce = announce or (lambda msg: print(msg, flush=True))

    # ---- one change to leave in place
    def request(self, spec: Mapping, applier=None, *, physical=None) -> dict:
        """Propose, wait for a decision, apply. Returns the applied proposal.

        `physical` is the rack photo audit (`wattnot.sam.physical.FleetAudit`). A power cap on
        a GPU that has no unit in its rack photo, or above the rack's per-node ceiling, is
        refused here, before any proposal exists. A cap that passes carries `checks.physical`.
        """
        if physical is not None:
            from wattnot.approvals.proposers import with_physical_check
            spec = with_physical_check(spec, physical)
            phys = spec["checks"].get("physical")
            if phys is not None and not phys["passed"]:
                raise NotApproved(f"physical check failed: {phys['detail']}; "
                                  "nothing was proposed or changed")
        if self.mode == DRY_RUN:
            self._need_simulated(spec)
        p = self.approvals.propose(spec)
        if p["status"] == m.PENDING and self.mode == DRY_RUN:
            p = self.approvals.approve(p["id"], "dry-run", via=svc.DRY_RUN,
                                       note="simulator; nothing real is changed")
        elif p["status"] == m.PENDING and self.mode == PRE_APPROVED:
            p = self.approvals.approve(p["id"], f"pre-approved:{self.name}",
                                       via=svc.PRE_APPROVED,
                                       note="started with the explicit pre-approved flag")
        p = self._wait(p)
        return self.approvals.apply(p["id"], by="wattnot", applier=applier)

    # ---- permission to try changes
    def session(self, *, title: str, bounds: Mapping, source: Mapping | None = None,
                ttl_s: float = 6 * 3600, reason: str = "", physical=None) -> TrialSession:
        """`physical` is the rack photo audit: GPUs with no unit in their photo are dropped
        from the grant, and the power cap range is cut to the rack's per-node ceiling."""
        if physical is not None:
            bounds = narrow_bounds(bounds, physical)
        return TrialSession(self, grant_spec(title=title, bounds=bounds, ttl_s=ttl_s,
                                             source=source or {}, reason=reason))

    # ---- shared
    def _need_simulated(self, spec: Mapping) -> None:
        labels = [s.get("label") for s in spec.get("savings", [])]
        if spec.get("kind") != m.TRIAL_GRANT and (
                not labels or any(x not in ("SIMULATED", "MOCK") for x in labels)):
            raise NotApproved("a dry-run gate only applies SIMULATED or MOCK proposals")

    def _decide_session(self, p: dict) -> dict:
        """Apply the mode's way of approving a trial grant."""
        if p["status"] != m.PENDING:
            return p
        if self.mode == PRE_APPROVED:
            return self.approvals.approve(p["id"], f"pre-approved:{self.name}",
                                          via=svc.PRE_APPROVED,
                                          note="started with the explicit pre-approved flag")
        if self.mode == DRY_RUN:
            return self.approvals.approve(p["id"], "dry-run", via=svc.DRY_RUN,
                                          note="simulator; nothing real is changed")
        return p

    def _wait(self, p: dict) -> dict:
        """Block until the proposal is approved. Raises NotApproved otherwise."""
        pid = p["id"]
        if p["status"] == m.PENDING:
            self.announce(
                f"waiting for approval: {p['title']}\n"
                f"  proposal {pid} (hash {p['content_sha256'][:12]}), "
                f"expires {p['expires_at']}\n"
                f"  review:  wattnot proposals show {pid}\n"
                f"  approve: wattnot proposals approve {pid}\n"
                f"  (folder: {self.approvals.store.root})")
        waited = 0.0
        while True:
            p = self.approvals.get(pid)
            if p["status"] in (m.APPROVED, m.APPLIED):
                return p
            if p["status"] != m.PENDING:
                raise NotApproved(f"{pid} is {p['status']}; nothing was changed")
            if waited >= self.wait_s:
                self.approvals.reject(pid, "wattnot", via="system",
                                      note=f"nobody approved it within {self.wait_s:g} s")
                raise NotApproved(f"{pid} was not approved within {self.wait_s:g} s; "
                                  "nothing was changed")
            self.sleep(self.poll_s)
            waited += self.poll_s


class TrialSession:
    """Context manager around one trial grant. Use `check_*` before every change."""

    def __init__(self, gate: ApprovalGate, spec: Mapping) -> None:
        self.gate, self.spec = gate, spec
        self.approvals = gate.approvals
        self.id: str | None = None
        self.bounds: Mapping = spec["change"]["bounds"]
        self._usage: dict = {"changes": 0, "by_kind": {}}
        self._since_flush = 0
        self._last_check = float("-inf")
        self._expires = 0.0

    @property
    def mode(self) -> str:
        return self.gate.mode

    def __enter__(self) -> TrialSession:
        g = self.gate
        p = self.approvals.propose(self.spec)
        p = g._decide_session(p)
        p = g._wait(p)
        if p["status"] == m.APPROVED:
            p = self.approvals.open_grant(p["id"], by=g.name or "wattnot", via=p["decision"]["via"])
        self.id = p["id"]
        self._expires = m.parse_iso(p["expires_at"])
        self._last_check = self.approvals.clock()
        return self

    def __exit__(self, *exc: object) -> None:
        if self.id is not None:
            self.approvals.close_grant(self.id, self._usage)

    # ---- checks (raise NotApproved; they never change anything themselves)
    def _alive(self) -> None:
        if self.id is None:
            raise NotApproved("no trial grant is open")
        now = self.approvals.clock()
        if now >= self._expires:
            raise NotApproved(f"grant {self.id} has expired")
        if now - self._last_check >= RECHECK_S:
            self._last_check = now
            status = self.approvals.store.get(self.id)["status"]
            if status != m.APPLIED:
                raise NotApproved(f"grant {self.id} was {status}; stopping")

    def _used(self, kind: str, value: float | None = None) -> None:
        u = self._usage
        u["changes"] += 1
        k = u["by_kind"].setdefault(kind, {"n": 0})
        k["n"] += 1
        if value is not None:
            k["min"] = value if "min" not in k else min(k["min"], value)
            k["max"] = value if "max" not in k else max(k["max"], value)
        self._since_flush += 1
        if self._since_flush >= FLUSH_EVERY and self.id:
            self._since_flush = 0
            self.approvals.save_usage(self.id, u)

    def check_cap(self, watts: float, gpu: int = 0) -> None:
        self._alive()
        c = self.bounds.get("power_cap_w")
        if not c:
            raise NotApproved("this grant does not allow power cap changes")
        if gpu not in self.bounds.get("gpus", [0]):
            raise NotApproved(f"GPU {gpu} is not in this grant")
        if not c["min"] <= watts <= c["max"]:
            raise NotApproved(f"{watts:g} W is outside the approved {c['min']}-{c['max']} W")
        self._used(m.POWER_CAP, float(watts))

    def check_vllm(self, params: Mapping | None = None) -> None:
        self._alive()
        if not self.bounds.get("vllm_config"):
            raise NotApproved("this grant does not allow changing the vLLM config")
        self._used(m.VLLM_CONFIG)

    def check_flex(self, target_pct: float, duration_s: float) -> None:
        self._alive()
        f = self.bounds.get("flex_shed")
        if not f:
            raise NotApproved("this grant does not allow a flex shed")
        if target_pct > f["max_target_pct"] or duration_s > f["max_duration_s"]:
            raise NotApproved(f"a {target_pct:g}% shed for {duration_s:g} s is outside the "
                              f"approved {f['max_target_pct']:g}% / {f['max_duration_s']:g} s")
        self._used(m.FLEX_SHED, float(target_pct))

    def check_moe(self, mass: float) -> None:
        """Adaptive top-k trial: `mass` is the cumulative router mass kept per token."""
        self._alive()
        r = self.bounds.get("moe_routing")
        if not r:
            raise NotApproved("this grant does not allow an adaptive top-k trial")
        if not r["min_mass"] <= mass <= r["max_mass"]:
            raise NotApproved(f"router mass {mass:g} is outside the approved "
                              f"{r['min_mass']:g}-{r['max_mass']:g}")
        self._used(m.MOE_ROUTING, float(mass))
