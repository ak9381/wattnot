# M05 PRD: Muse Spark planner, Optuna warm start and ablation
Depends on: M03, M06 · Estimate: 9 h · Contracts: `schemas/SCHEMAS.md`

## Why
Muse Spark 1.3 proposes the first ~4 trials and narrows the bounds (warm start, LLAMBO cited as prior art), Optuna takes over, and Spark writes one plain 'why' per trial. Then prove it helps: cold vs warm vs grid, measured in wall-clock and Wh to reach the best point.

## Requirements
- FR-PLAN-01 Spark gets only numbers: capabilities, baseline metrics, knob ranges, past trial results. No prompts or outputs from the workload.
- FR-PLAN-02 Structured output: `{trials:[{params, reason}], bounds:{…}}`; validate against the TrialParams schema; on invalid output fall back to Optuna defaults and log it.
- FR-PLAN-03 Optuna TPE seeded with Spark trials via `study.enqueue_trial`; narrowed bounds applied.
- FR-PLAN-04 Every proposal emits `trial.proposed` with `source` and `reason`, and lands in the receipt `planner_trace`.
- FR-PLAN-05 Ablation: cold Optuna vs Spark warm start vs grid, same budget, report trials, wall-clock and Wh to best. Publish even if warm start loses.
- FR-PLAN-06 Spend: planner calls counted against the Meta spend log; ablation uses cached Spark proposals so re-runs cost nothing.

## Out of scope
Spark editing code or configs directly. Any Meta call during development (use the recorded fixture).

## Done means
Planner trace in every receipt; ablation chart with three arms and time/Wh budgets; total Spark spend for the ablation < $2 (logged).

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
