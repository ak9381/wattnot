# M06 PRD: Quality and p99 gates, Muse Spark judge, noise-floor Welch test
Depends on: M03 · Estimate: 7 h · Contracts: `schemas/SCHEMAS.md`

## Why
Never ship a saving that hurts quality or latency, and never bill a saving that is just noise.

## Requirements
- FR-GATE-01 Fixed checks first: exact/regex/unit-test checks on the public replay set (Muse Code tasks run their tests). Pass rate must be ≥ baseline − 1 point.
- FR-GATE-02 Judge second: Muse Spark 1.3 compares candidate vs baseline outputs on a 30-item public subset; publish judge-human agreement on those 30.
- FR-GATE-03 p99 gate: candidate p99 ≤ baseline p99 × (1 + slack), default slack 10%, or the SLA from the slider.
- FR-GATE-04 Noise gate: Welch one-sided t-test on per-rep Wh/request (α 0.05). Bill the lower bound of a 95% bootstrap CI of the saving (10,000 resamples). p ≥ 0.05 → grade NOISE, not billed.
- FR-GATE-05 Any failed gate emits `gate.result passed=false`, and in Act mode triggers `action.rolled_back`.
- FR-GATE-06 Judge only sees public items; nothing confidential goes to Meta.

## Out of scope
Human eval beyond the 30-item agreement set.

## Done means
Every trial has 3–4 `gate.result` events; receipts carry welch + CI; a seeded bad trial is blocked and rolled back in the demo run.

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
