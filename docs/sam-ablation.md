# SAM on vs off: rack photo audit

Replayed offline from saved SAM 3.1 output. Photos and unit counts are MEASURED. The inventory, rack budgets and the fake GPU's response curve are SIMULATED. Ceilings are ESTIMATE (GPU power only).

| Rack | Inventory lists | SAM sees | Ceiling, SAM off | Ceiling, SAM on | Notes |
|---|---|---|---|---|---|
| R1 | 8 | 10 (MEASURED) | 700 W | 600 W | unlisted slots [8, 9]; trusting the inventory would overdraw by 1000 W (ESTIMATE) |
| R2 | 6 | 4 (MEASURED) | 466 W | 700 W | GPUs [12, 13] have no unit; 2 of 4 units inferred from stack height |

GPUs the planner and gate may touch: **14** with SAM off, **12** with SAM on (missing: [12, 13]).

## Same cap request, two answers

| GPU | Cap asked | SAM off | SAM on |
|---|---|---|---|
| 0 | 650 W | allowed (listed at slot 0 (inventory only, no photo check); per-node ceiling 700 W (ESTIMATE)) | refused (650 W is above the rack's per-node ceiling of 600 W) |
| 0 | 550 W | allowed (listed at slot 0 (inventory only, no photo check); per-node ceiling 700 W (ESTIMATE)) | allowed (unit seen at slot 0; per-node ceiling 600 W (ESTIMATE)) |
| 8 | 500 W | refused (500 W is above the rack's per-node ceiling of 466 W) | allowed (unit seen at slot 0; per-node ceiling 700 W (ESTIMATE)) |
| 12 | 450 W | allowed (listed at slot 4 (inventory only, no photo check); per-node ceiling 466 W (ESTIMATE)) | refused (no unit outlined at slot 4 in the R2 photo) |

## Planner, start cap 700 W (SIMULATED trial)

| GPUs | Final cap, SAM off | Final cap, SAM on |
|---|---|---|
| [0, 1, 2, 3, 4, 5, 6, 7] | 700 W | 600 W |
| [8, 9, 10, 11] | 466 W | 700 W |
| [12, 13] | 466 W | refused |
