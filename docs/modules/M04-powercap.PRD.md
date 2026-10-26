# M04 PRD: Power-cap and clock controller (root mode)
Depends on: M01, M02, M03 · Estimate: 6 h · Contracts: `schemas/SCHEMAS.md`

## Why
On a rented root GPU, sweep power caps and SM clocks to build the energy vs latency Pareto front (5 vs 5 reps per point), and run a live ~100 ms cap controller on one GPU.

## Requirements
- FR-CAP-01 Sweep caps from max down in steps (e.g. 700, 600, 500, 400, 350, 300 W on H100) and optional locked SM clocks.
- FR-CAP-02 Each point: 5 reps, 10 Hz CSV, same workload as baseline. Publish the null result if nothing beats noise.
- FR-CAP-03 Live controller: every 100 ms read p99 window and power; lower the cap while p99 has headroom, raise it when p99 nears the SLA. Hysteresis and min/max bounds.
- FR-CAP-04 Always restore default cap and clocks on exit, error or SIGTERM.
- FR-CAP-05 Independent re-run: repeat the best point on a second GPU or publish a Modal receipt anyone can re-run.
- FR-CAP-06 Optional p99 SLA slider (demo, from buffer): picks a point from the stored front, no live GPU needed.

## Out of scope
2-GPU power-limit test and rebalancer (roadmap). Phase-aware clocks (roadmap).

## Done means
On the rental: Pareto CSVs for ≥ 5 cap points × 5 reps, idle before/after, best point re-run; GPU left at default cap (read back).

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
