# M06 TDD: Quality and p99 gates, Muse Spark judge, noise-floor Welch test
Branch: `feat/m06-gates` · Hours: 7 · Depends on: M03

## Interfaces
```python
def fixed_checks(outputs, items) -> GateResult
def judge(base_out, cand_out, items, model="muse-spark-1.3") -> GateResult   # recorded fixture in tests
def p99_gate(base: TrialResult, cand: TrialResult, slack=0.10) -> GateResult
def noise_gate(base_wh: list[float], cand_wh: list[float], alpha=0.05, n_boot=10000, seed=7) -> GateResult  # detail: t, df, p_value, ci_low, ci_high
```

## Data schemas
Uses the canonical run event (`schemas/run-event.schema.json`) and receipt (`schemas/receipt.schema.json`). Module-local formats are defined under Interfaces. Never add fields to the canonical schemas inside this module; open a schema task instead.

## File layout (this module owns only these paths)
`wattnot/gates/{fixed,judge,latency,noise}.py`, `data/judge-30.jsonl` (public), `tests/test_gates.py`

## Test plan (tests first; write, watch fail, then pass)
Welch test against scipy reference values; bootstrap CI with fixed seed is stable; identical samples → NOISE; clear 10% drop with sd 1% → billable. Rollback event fires on a failed gate.

## Acceptance criteria
Every trial has 3–4 `gate.result` events; receipts carry welch + CI; a seeded bad trial is blocked and rolled back in the demo run.
- `make test` green, `ruff`/`tsc` clean, and the branch pushed.

## Dependencies
M03

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
