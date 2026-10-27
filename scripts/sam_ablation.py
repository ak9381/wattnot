"""Replay the rack photo audit with SAM on and off, and write the results.

    python scripts/sam_ablation.py            # offline: saved SAM output, no key, no spend

Writes docs/sam-ablation.json, docs/sam-ablation.md and rack/audit/<rack>.audit.jpg (the last
needs Pillow).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from wattnot.sam import ablation
from wattnot.sam import physical as ph

ROOT = Path(__file__).resolve().parents[1]


def table(r: dict) -> str:
    out = ["# SAM on vs off: rack photo audit", "",
           "Replayed offline from saved SAM 3.1 output. Photos and unit counts are MEASURED. "
           "The inventory, rack budgets and the fake GPU's response curve are SIMULATED. "
           "Ceilings are ESTIMATE (GPU power only).", "",
           "| Rack | Inventory lists | SAM sees | Ceiling, SAM off | Ceiling, SAM on | Notes |",
           "|---|---|---|---|---|---|"]
    for k in r["racks"]:
        on, off = k["sam_on"], k["sam_off"]
        notes = []
        if on["unlisted_slots"]:
            notes.append(f"unlisted slots {on['unlisted_slots']}; trusting the inventory would "
                         f"overdraw by {on['over_budget_w_if_inventory_trusted']} W (ESTIMATE)")
        if on["unverified_gpus"]:
            notes.append(f"GPUs {on['unverified_gpus']} have no unit")
        if on["inferred_units"]:
            notes.append(f"{on['inferred_units']} of {on['nodes']} units inferred "
                         "from stack height")
        out.append(f"| {k['rack']} | {k['inventory_nodes']} | {on['nodes']} (MEASURED) "
                   f"| {off['ceiling_w']} W | {on['ceiling_w']} W | {'; '.join(notes)} |")
    e = r["eligible_gpus"]
    out += ["", f"GPUs the planner and gate may touch: **{len(e['sam_off'])}** with SAM off, "
            f"**{len(e['sam_on'])}** with SAM on (missing: "
            f"{sorted(set(e['sam_off']) - set(e['sam_on']))}).", "",
            "## Same cap request, two answers", "",
            "| GPU | Cap asked | SAM off | SAM on |", "|---|---|---|---|"]
    for g in r["gate_probes"]:
        def cell(x):
            return ("allowed" if x["allowed"] else "refused") + f" ({x['detail']})"
        out.append(f"| {g['gpu']} | {g['sam_off']['cap_w']} W | {cell(g['sam_off'])} "
                   f"| {cell(g['sam_on'])} |")
    out += ["", "## Planner, start cap 700 W (SIMULATED trial)", "",
            "| GPUs | Final cap, SAM off | Final cap, SAM on |", "|---|---|---|"]
    groups: dict = {}
    for p in r["planner_final_cap_w"]:
        a, b = p["sam_off"], p["sam_on"]
        key = (a.get("cap_w"), "refused" if "refused" in b else b["cap_w"])
        groups.setdefault(key, []).append(p["gpu"])
    for (a, b), gs in groups.items():
        out.append(f"| {gs} | {a} W | {b if b == 'refused' else f'{b} W'} |")
    out.append("")
    return "\n".join(out)


def main() -> int:
    specs, _ = ph.load_fleet(ROOT / "rack" / "audit" / "fleet.json")
    r = ablation.run(specs)
    (ROOT / "docs" / "sam-ablation.json").write_text(json.dumps(r, indent=2) + "\n")
    (ROOT / "docs" / "sam-ablation.md").write_text(table(r))
    try:
        from wattnot.sam.overlay import render
        for a in ph.audit_fleet(specs, sam=True).racks:
            print("wrote", render(a, ROOT / "rack" / "audit" / f"{a.spec.id}.audit.jpg"))
    except ImportError:
        print("Pillow not installed: skipped the overlay images", file=sys.stderr)
    print((ROOT / "docs" / "sam-ablation.md").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
