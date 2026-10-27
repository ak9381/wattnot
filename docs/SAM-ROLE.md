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

# Room scan: SAM on a data-centre room, with a thermal frame

The rack audit above reads one rack. The room scan reads a whole room, and it is step 1 of a run.
The question it answers: which racks are hot, and is the heat because the GPUs work hard (cap them)
or because cooling failed (do not cap, call someone)?

## The idea in four lines

1. SAM 3.1 cuts every rack out of the room photo (prompt `server rack`, one call, $0.0025).
2. A thermal frame gives each rack mask a temperature (p90 of the pixels inside the mask, read back
   to °C with the colour legend).
3. GPU telemetry gives each rack its power. A camera cannot see watts, so this part is not optional.
4. Cross-check: hot and high power → lower the cap ceiling (the planner and the gate still decide).
   Hot and low power → cooling-fault flag, hold, no cap. Not hot → leave it alone.

Without the masks the hot pixel belongs to no rack and no GPUs. The ablation toggle in the view shows
the cost: the blunt fallback cuts every GPU by the same watts, hits 11 racks (8 needlessly) and
misses the cooling fault.

## What is real and what is not

| Part | Label |
| --- | --- |
| Rack count and outlines (ARSAT photo: 11) | MEASURED (SAM 3.1 output, saved under `rack/room/arsat/sam-raw`) |
| Power of the one real H100 (GPU 8 of rack R03, star in the view) | MEASURED in RUN1, replayed |
| Power of every other GPU | REPLAY of that trace, scaled by a SIMULATED load factor. Those racks are marked `example` |
| Thermal frame in the default view | SIMULATED, painted from the power with inlet + R × power + fault. Watermarked inside the image |
| Camera °C per rack | SIMULATED here (read from the frame); ESTIMATE when the frame is a real IR still |
| GPU °C | SIMULATED. RUN1 has no GPU temperature log |
| Rack to GPU map | SIMULATED. The photo has no GPU inventory |
| After (post-cap) temperatures | SIMULATED, 0.05 °C per W in the model |
| Decisions | ADVICE |

Honest limits: a normal CCTV camera sees no heat, so a thermal camera or an IR still is needed in
production. We have one real GPU, so every other GPU in the room is an example. The real IR still we
could license (MDPI Energies 2024, 17(8), 1835, Fig. 3, CC BY 4.0) is a different scene from the
visible photo, so the panels are not registered: SAM found 0 racks on the false-colour image (11
prompts tried), the masks come from the visible photo, and the IR still is used only to check the
colour-to-°C reading. Its range of 20 to 41.5 °C is taken from the figure's own legend, so the maximum
is calibration, not validation, and FLIR on-screen text reads as hot.

## Photos and licences

| Room | Photo | Licence | SAM racks |
| --- | --- | --- | --- |
| `arsat` (used in the view) | "Datacenter de ARSAT.jpg", IMarcoHerrera, Wikimedia Commons | CC BY-SA 4.0 | 11 |
| `insidesite` (kept as evidence) | Switch and Data data centre, Wikimedia Commons | CC BY-SA 4.0 | 15 |
| `irpair` | Ding et al., Energies 2024, 17(8), 1835, Fig. 3 | CC BY 4.0 | 0 on the false-colour panel |

Credits are in `rack/room/*/ATTRIBUTION.md`. Overlays are derivative works under the same licence.

## Run it

```
pip install -e '.[vision]'
python scripts/room_scan.py --room arsat                       # replay saved SAM output, writes web/room/data/arsat
python scripts/room_scan.py --room arsat --live                # 1 SAM call, ledger-guarded
python scripts/room_scan.py --room arsat --thermal ir.jpg --range 20 41.5   # a real IR still or CCTV thermal frame
python scripts/room_scan.py --room arsat --events ROOT --run-id run_20261027_180000_abc123
python -m http.server 8766 --directory web                      # then open /room/index.html
```

`--thermal` replaces the simulated frame with your own image and reads °C from its colour bar
(`--range LO HI` is the legend's low and high). Scenario inputs (inlet, R, loads, the fault on R04,
the thresholds) live in `rack/room/scenario.json`.

## Room scan JSON (`wattnot.room/1`)

Written to `web/room/data/<room>/scan.json`, or call `wattnot.sam.room.build_scan(...)` and
`room.public(scan)`.

```
{ format: "wattnot.room/1", room_id, image, thermal_image, thermal_after_image|null, size: [w, h],
  labels: { rack_count, rack_outline, rack_to_gpu_map, thermal_frame, rack_temp_c, gpu_power_w,
            gpu_temp_c, decision, after, load_factors, example_racks, h100_gpu_power_w },
  thermal: { mode: "simulated"|"ir_still", range_c: [lo, hi], method, hot_c, high_power_w,
             t_in_c, slope_c_per_w, t_index },
  sam: { model, prompt, racks_found },
  power_source, real_rack: "R03"|null,
  racks: [ { id, box, polygon: [[x, y], ...], path, temp_c, temp_max_c, power_w,
             source: "h100-run1"|"example",
             gpus: [ { id, power_w, gpu_temp_c, measured } ],
             decision: "derate"|"hold"|"ok"|"none", cap_w|null, reason,
             before: { temp_c, power_w }, after: { temp_c, power_w }, series_w } ],
  hotspots: [ rack id, hottest first ],
  before: { max_c, mean_c, hot_racks, room_power_w },
  after:  { max_c, mean_c, hot_racks, room_power_w, saved_w },
  sam_off: { action, cut_w, reason, racks_cut, racks_cut_needlessly, cooling_faults_missed,
             watts_lost, watts_lost_with_sam } }
```

Images are file names inside the same folder. `polygon` is for outlines; `path` is the run-length
mask for the clickable overlay.

## Events (for the run flow and Muse Spark in RUN2)

`room.emit_events(scan, emitter)` writes the scan into a run's `events.jsonl` with event types the
frozen `run-event.schema.json` already has, so nothing else has to change:

| seq | type | step_id | data |
| --- | --- | --- | --- |
| 0 | `step.started` | `room-scan` | `kind: "room_scan"`, label, model `sam-3.1` |
| 1 | `step.progress` | `room-scan` | `kind: "room_scan"`, `phase: "before"`, room_id, image, thermal_image, labels, hot_c, range_c, racks, summary, hotspots, `targets`, sam |
| 2 | `step.progress` | `room-scan` | same shape, `phase: "after"` (post-cap temps and power), `targets: null` |
| 3 | `step.finished` | `room-scan` | status, `meta_cost_usd: 0.0025` |

A rack in `racks` is `{id, polygon, temp_c, power_w, gpus: [ids], decision, cap_w, reason, source}`.
Consumers should key on `data.kind == "room_scan"`. If the schema owners prefer a first-class
`room_scan` event type, the payload can move unchanged.

`room.targets(scan)` is the list Muse Spark sees for target picking: racks ordered `derate`, then
`hold`, then `ok`, with GPU ids, camera °C, power, proposed cap, headroom to the hot line and the
reason. Numbers and ids only, no images. The planner reads the same facts through
`RackAudit.thermal`: a `derate` lowers that rack's `ceiling_w`, a `hold` makes its GPUs ineligible,
so a proposal above the ceiling or on a held GPU is refused, and the p99 gate still has the last word.

## Showing it

* Full view: `web/room/index.html` (visible/thermal, before/after, SAM on/off, °C legend,
  hotspot list, per-rack drawer). `mount(root, {room, data, scan})` embeds it.
* Architecture-map node: `web/room/node.html` is a demo. In the map page call
  `mountNode(el, {room: "arsat", data: "../room/data", href})` from `web/room/node.js`, or pass a
  `room_scan` event payload as `scan` with `base` set to the image folder.
  It has the same visible/thermal and before/after toggles. The H100 rack carries a star and the
  `MEASURED` pill; every other rack says `example`.

Screenshots: `docs/img/room-thermal-before.png`, `room-visible.png`, `room-thermal-after.png`,
`room-sam-off.png`, `room-r03.png` (the H100 rack), `room-node-thermal.png`, `room-node-after.png`.
