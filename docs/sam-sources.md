# SAM 3.1 inputs: photos, sources, licences

| Rack dir | Photo | Source | Licence | Used in ablation |
|---|---|---|---|---|
| rack/cisco | real photo, Cisco HyperFlex HXAF240c M5 nodes | https://commons.wikimedia.org/wiki/File:Rack_of_Cisco_Hyperflex_HXAF_240c_M5_nodes.jpg | CC BY-SA 4.0 | yes (R1) |
| rack/hpe | real photo, HPE ProLiant DL360 Gen10 nodes | https://commons.wikimedia.org/wiki/File:HPE_ProLiant_DL360_Gen10_nodes_in_MAM_cluster.jpg | CC BY-SA 4.0 | yes (R2) |
| rack/ai | AI-generated illustration, not a real rack | made for this repo | repo licence | no (layout test only) |

Each folder's `ATTRIBUTION.md` has the full credit line. SAM output is saved in `rack/*/sam-raw`, so
`python scripts/sam_ablation.py` replays it with no key and no spend.

## Does SAM change a decision?

`docs/sam-ablation.json`: with SAM on, the planner's final cap changes for 14 of 14 GPUs and two GPUs
drop out as not seen in the photo. The unit counts are MEASURED from the photos, but the inventory,
rack budgets and node power limits they are checked against are SIMULATED (`rack/audit/fleet.json`).

So the decision change is real only against a made-up inventory. SAM stays **off the main path**:
it is shown in the dashboard only when "Illustrations" is switched on. It moves onto the main path
once a real fleet inventory is plugged in and the same on/off ablation still changes a decision.

More photos: add a folder under `rack/` with `rack.jpg` and `ATTRIBUTION.md` (source URL and a
permissive licence: CC0, CC BY or CC BY-SA), run SAM once, and commit `sam-raw`.
