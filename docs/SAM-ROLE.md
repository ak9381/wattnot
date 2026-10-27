# What SAM does in WattNot: the rack photo audit

SAM 3.1 looks at a photo of each rack and counts the servers that are really there. That
count decides three things WattNot used to take on trust from an inventory file:

1. **Which GPUs it may touch.** A GPU whose slot has no unit in the photo is left alone.
   Trial grants, cap proposals and the planner all refuse it.
2. **How hard a node may be capped.** The per-node ceiling is the rack's power budget divided
   by the nodes actually seen, not the nodes the inventory claims.
3. **What the report says.** Recommendations use the photographed node count, list units the
   inventory does not know about, and say so when a ceiling forced a cap change.

If SAM sees nothing, or fewer units than the inventory, WattNot does less, never more. A missed
unit means "unverified, do not touch". An extra box only lowers the ceiling.

## How it works

```
rack.jpg ──SAM 3.1──▶ boxes ──▶ units (slot 0 = top) ──┐
                                                       ├─▶ audit ─▶ planner · approval gate · recommendations
inventory (slot → GPU), rack budget, node power ───────┘
```

- `wattnot.sam.client.SamClient` sends one concept prompt per call (`1U server`,
  `rack server`, `server`). Each call is booked in the spend ledger first ($0.0025 per image)
  and refunded if the API errors. The Meta key is never written to the ledger.
- `wattnot.sam.physical.units_from_raw` turns the boxes into units. It uses the prompt that
  gave the most unit-sized boxes, takes the median box height as the unit pitch, drops a box
  that covers the whole rack, and merges duplicates (IoU 0.5). When SAM outlines two or more
  units as one block, the block is cut into pitch-sized units and those are marked
  `inferred`. Gaps become empty slots, so slot numbers stay true.
- `RackAudit` compares slots seen with the inventory: **verified**, **unverified** (listed, no
  unit seen) and **unlisted** (unit seen, not listed). `ceiling_w = min(node_max_w,
  budget_w // nodes_seen)`.

## Where it bites

| Place | What changes | Code |
|---|---|---|
| Planner | The cap range tops out at the ceiling. If the GPU starts above it, the first step is to the ceiling, through the same trial and gate as any step. If that step fails the gate it is rolled back and the loop stops. | `control/planned.py`, `CapGuard.tighten_max` |
| Approval gate | `request(spec, physical=audit)` refuses a cap on an unverified GPU or above the ceiling before a proposal exists. A cap that passes carries `checks.physical`. `session(..., physical=audit)` narrows the grant to verified GPUs and the ceiling. | `approvals/gate.py` |
| Auto-approve policy | A proposal whose `checks.physical` did not pass is never auto-approved. | `approvals/policy.py` |
| Recommendations | `gpus_per_rack` and the node count come from the photo. New items: nodes seen (MEASURED), ceiling and over-budget watts (ESTIMATE), and plain advice for unverified or unlisted units. | `sam/physical.py`, `recommend.py` |
| LLM planner input | The summary sent to the planner model carries a `physical` block. | `planner/spark.py` |

## Results on the demo fleet

Run `python scripts/sam_ablation.py` (offline, free). Full table: [sam-ablation.md](sam-ablation.md).

| Rack | Inventory lists | SAM sees | Ceiling, SAM off | Ceiling, SAM on |
|---|---|---|---|---|
| R1 (Cisco photo) | 8 nodes | 10 units (MEASURED) | 700 W | 600 W |
| R2 (HPE photo) | 6 nodes | 4 units, 2 of them inferred (MEASURED) | 466 W | 700 W |

- With SAM off the planner and gate may touch 14 GPUs. With SAM on, 12. GPUs 12 and 13 have no
  unit in the R2 photo.
- Trusting the R1 inventory would let 10 real nodes run at 700 W each against a 6,000 W
  budget: 1,000 W over (ESTIMATE).
- The inventory also over-counts R2, so with SAM off its GPUs were capped at 466 W for no reason.
- A 650 W cap on GPU 0 is allowed with SAM off and refused with SAM on. A 450 W cap on GPU 12
  is allowed with SAM off and refused with SAM on.
- `tests/test_sam_physical.py` deletes one SAM box from the saved output and checks that its GPU
  turns unverified.

Overlays: `rack/audit/R1.audit.jpg`, `rack/audit/R2.audit.jpg`.

## What is real and what is not

| Thing | Label |
|---|---|
| The two photos, SAM's boxes, the unit counts | MEASURED |
| Slot-to-GPU map, the claimed node counts, rack budgets, node power limits (`rack/audit/fleet.json`) | SIMULATED. Made up so the audit has something to find. Replace with your own. |
| The per-node ceiling, the over-budget watts | ESTIMATE. Budget divided by nodes, GPU power only. No CPU, fan or PSU loss. |
| The planner trial numbers in the ablation | SIMULATED response curve |

Limits to know:

- SAM's concept prompts work best on a straight-on photo with clear gaps between units. On
  dense or angled stacks it merges units or misses some. WattNot copes by cutting merged
  blocks by height (marked `inferred`) and by treating misses as "do not touch".
- Slot numbers count from the top unit seen. The demo maps slot to GPU in that order, with one
  GPU per unit. A real inventory needs a mount-height reference, such as a rack-unit ruler
  or a labelled top unit.
- The audit says a unit is there, not that it is the right machine. It is a check on the
  inventory, not a replacement for one.
- We tried reading status LEDs with a `LED` prompt. It hit 11 on one photo, 4 boxes on only 2 of
  4 units on another, and 0 on a third, so it is not used.

## Photos and licences

Both photos come from Wikimedia Commons under CC BY-SA 4.0. See `rack/cisco/ATTRIBUTION.md` and
`rack/hpe/ATTRIBUTION.md`. The overlays are derivative works under the same licence.

## Run it

```
pip install -e '.[vision]'
python scripts/sam_audit.py                  # replay saved SAM output
python scripts/sam_audit.py --live rack/mine # 3 SAM calls (about $0.0075), ledger-guarded
python scripts/sam_ablation.py               # SAM on vs off, writes docs/sam-ablation.* and overlays
```
