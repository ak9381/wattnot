# M04 TDD: Power-cap and clock controller (root mode)
Branch: `feat/m04-powercap` · Hours: 6 · Depends on: M01, M02, M03

## Interfaces
```python
class CapController:
    def __init__(self, gpu:int, min_w:int, max_w:int, p99_sla_ms:float, step_w:int=25, period_ms:int=100)
    def set_cap(self, watts:int) -> None   # emits action.applied
    def restore(self) -> None
    def loop(self, stop: Event) -> None
def sweep(caps:list[int], clocks:list[int|None], reps:int=5) -> list[TrialResult]
```

## Data schemas
Uses the canonical run event (`schemas/run-event.schema.json`) and receipt (`schemas/receipt.schema.json`). Module-local formats are defined under Interfaces. Never add fields to the canonical schemas inside this module; open a schema task instead.

## File layout (this module owns only these paths)
`wattnot/control/{nvml_ctl,controller,sweep}.py`, `notebooks/pareto.ipynb`, `tests/test_controller.py`

## Test plan (tests first; write, watch fail, then pass)
Fake NVML with a power/latency model: controller converges and never sets out of range; restore runs on exception and SIGTERM; hysteresis stops flapping (≤ 2 changes/s).

## Acceptance criteria
On the rental: Pareto CSVs for ≥ 5 cap points × 5 reps, idle before/after, best point re-run; GPU left at default cap (read back).
- `make test` green, `ruff`/`tsc` clean, and the branch pushed.

## Dependencies
M01, M02, M03

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
