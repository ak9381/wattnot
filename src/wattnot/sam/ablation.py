"""SAM on versus SAM off, on the same fleet, the same planner and the same gate.

Offline: it replays the saved SAM output in rack/*/sam-raw. The inventory, the rack budgets and
the response curve of the fake GPU are SIMULATED. The unit counts come from the photos.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from types import SimpleNamespace

from wattnot.approvals.proposers import physical_check
from wattnot.control import CapGuard
from wattnot.control.planned import Measured, planned_loop
from wattnot.planner import DecisionMaker
from wattnot.sam import physical as ph

KNOBS = {"max_num_seqs": 256, "enable_prefix_caching": True, "quantization": "none"}
START_CAP_W = 700
# Gate a cap is judged on: p99 may rise 10% over the baseline. SIMULATED curve below.
P99_LIMIT = 1.10


class _Gpu:
    """The three NVML calls CapGuard uses, held in memory."""

    def __init__(self, start_w: int, lo_w: int = 200, hi_w: int = 700) -> None:
        self.mw, self.lo, self.hi = start_w * 1000, lo_w * 1000, hi_w * 1000

    def power_limit_constraints_mw(self, _h):
        return self.lo, self.hi

    def power_limit_mw(self, _h):
        return self.mw

    def set_power_limit_mw(self, _h, mw):
        self.mw = mw


def _trial(cap: int, knobs: dict) -> Measured:
    """SIMULATED: no slowdown down to 400 W, then p99 climbs 0.4% per watt lost."""
    p99 = 1.0 if cap >= 400 else 1.0 + (400 - cap) * 0.004
    return Measured(p99, 0.048 * cap / 700 + 0.014)


def _gate(m: Measured):
    ok = m.p99_s <= P99_LIMIT
    return SimpleNamespace(passed=ok, failed=[] if ok else ["p99"])


def _planner(fleet: ph.FleetAudit, gpu: int) -> dict:
    gpu_dev = _Gpu(START_CAP_W)
    try:
        with CapGuard(gpu_dev, 0, handle_sigterm=False) as g:
            res = planned_loop(g, DecisionMaker(None), baseline=_trial(START_CAP_W, KNOBS),
                               trial=_trial, gate=_gate, knobs=KNOBS, slack=0.1,
                               physical=(fleet, gpu))
            return {"cap_w": g.cap_w(), "highest_allowed_w": g.top_w,
                    "ceiling_step": res.physical,
                    "advice": [i.text for i in res.recommendations.actions
                               if i.label == "ADVICE"]}
    except ph.NotVerified as e:
        return {"refused": str(e), "cap_w": START_CAP_W}


def _gate_verdict(fleet: ph.FleetAudit, gpu: int, cap_w: int) -> dict:
    chk = physical_check(fleet, gpu, cap_w)
    return {"cap_w": cap_w, "allowed": bool(chk["passed"]), "detail": chk["detail"]}


@dataclass(frozen=True)
class Probe:
    gpu: int
    cap_w: int


PROBES: Sequence[Probe] = (Probe(0, 650), Probe(0, 550), Probe(8, 500), Probe(12, 450))


def run(specs: Sequence[ph.RackSpec]) -> dict:
    off = ph.audit_fleet(specs, sam=False)
    on = ph.audit_fleet(specs, sam=True)
    racks = []
    for a, b in zip(off.racks, on.racks, strict=True):
        racks.append({
            "rack": a.spec.id, "photo": f"rack/{a.spec.directory.split('/')[-1]}/{a.spec.photo}",
            "inventory_nodes": len(a.spec.declared), "budget_w": a.spec.budget_w,
            "sam_off": {"nodes": a.present_nodes, "ceiling_w": a.ceiling_w},
            "sam_on": {"nodes": b.present_nodes, "ceiling_w": b.ceiling_w,
                       "unverified_gpus": list(b.unverified), "unlisted_slots": list(b.unlisted),
                       "inferred_units": sum(u.how == "inferred" for u in b.scan.units),
                       "over_budget_w_if_inventory_trusted": b.over_budget_w_if_trusting_inventory},
        })
    gpus = sorted(g for a in on.racks for g in (*a.verified, *a.unverified))
    planner = [{"gpu": g, "sam_off": _planner(off, g), "sam_on": _planner(on, g)} for g in gpus]
    gates = [{"gpu": p.gpu, "sam_off": _gate_verdict(off, p.gpu, p.cap_w),
              "sam_on": _gate_verdict(on, p.gpu, p.cap_w)} for p in PROBES]
    changed = [p["gpu"] for p in planner if p["sam_off"] != p["sam_on"]]
    return {
        "labels": {"photos_and_unit_counts": "MEASURED",
                   "inventory_budgets_node_power": "SIMULATED", "ceiling_w": "ESTIMATE",
                   "trial_response_curve": "SIMULATED"},
        "racks": racks,
        "eligible_gpus": {"sam_off": off.eligible_gpus(), "sam_on": on.eligible_gpus()},
        "planner_final_cap_w": planner,
        "gate_probes": gates,
        "gpus_whose_planner_outcome_changed": changed,
        "gpus_total": len(gpus),
    }

