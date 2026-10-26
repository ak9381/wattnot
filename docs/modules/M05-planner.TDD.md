# M05 TDD: Muse Spark planner, Optuna warm start and ablation
Branch: `feat/m05-planner` · Hours: 9 · Depends on: M03, M06

## Interfaces
```python
def propose(ctx: PlannerContext, n:int=4) -> list[Proposal]   # Proposal(params: TrialParams, reason: str)
def make_study(seed_trials: list[Proposal], bounds: Bounds) -> optuna.Study
def ablation(budget_trials:int, arms=("cold","warm","grid")) -> AblationReport  # per arm: trials_to_best, seconds_to_best, wh_to_best
```
Meta call: `POST https://api.meta.ai/v1/chat/completions`, model `muse-spark-1.3`, JSON structured output, low reasoning effort. Key from env `MODEL_API_KEY`.

## Data schemas
Uses the canonical run event (`schemas/run-event.schema.json`) and receipt (`schemas/receipt.schema.json`). Module-local formats are defined under Interfaces. Never add fields to the canonical schemas inside this module; open a schema task instead.

## File layout (this module owns only these paths)
`wattnot/planner/{spark,optuna_study,ablation}.py`, `tests/fixtures/spark_plan_recorded.json`, `tests/test_planner.py`

## Test plan (tests first; write, watch fail, then pass)
All tests use the recorded Spark response (no network). Invalid JSON → fallback path. Enqueued trials appear first in the study. Ablation math on a synthetic objective.

## Acceptance criteria
Planner trace in every receipt; ablation chart with three arms and time/Wh budgets; total Spark spend for the ablation < $2 (logged).
- `make test` green, `ruff`/`tsc` clean, and the branch pushed.

## Dependencies
M03, M06

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
