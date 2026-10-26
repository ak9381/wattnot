# M02 PRD: Telemetry meter (NVML/DCGM) and 10 Hz traces
Depends on: M01 · Estimate: 5 h · Contracts: `schemas/SCHEMAS.md`

## Why
Measure GPU energy honestly. Sample power at 10 Hz, prefer the hardware energy counter for Wh, and write raw CSVs anyone can re-check.

## Requirements
- FR-TEL-01 Sample `power_w`, clocks, temp, util at 10 Hz per GPU in a background thread.
- FR-TEL-02 Wh per window from the NVML total-energy counter (mJ) when present; fall back to trapezoid integration of power and label it.
- FR-TEL-03 Write `traces/<run_id>/<trial>_<rep>.csv` with a SHA-256 next to it.
- FR-TEL-04 Emit `telemetry.sample` events at 1 Hz (downsampled) for the UI.
- FR-TEL-05 Optional DCGM and Zeus backends behind the same interface (`--meter nvml|dcgm|zeus`).
- FR-TEL-06 Idle power before/after: 60 s idle window at run start and end.
- FR-TEL-07 Per-request attribution by token share with a cap (credit Neuralwatt's method in the README).

## Out of scope
Whole-node or wall-plug power. Water and carbon (those are ESTIMATE fields computed in M7/M12).

## Done means
10 Hz CSV produced for every rep; `energy_mj` used when available; Wh matches a hand calculation on the sample trace.

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
