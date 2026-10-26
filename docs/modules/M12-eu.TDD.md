# M12 TDD: EU report export (Delegated Reg. 2024/1364 fields)
Branch: `feat/m12-eu` · Hours: 3 · Depends on: M07

## Interfaces
`wattnot report eu --receipts receipts/*.json --out reports/eu-2024-1364.{csv,html}`

## Data schemas
Uses the canonical run event (`schemas/run-event.schema.json`) and receipt (`schemas/receipt.schema.json`). Module-local formats are defined under Interfaces. Never add fields to the canonical schemas inside this module; open a schema task instead.

## File layout (this module owns only these paths)
`wattnot/report/eu.py`, `wattnot/report/templates/eu.html`, `tests/test_eu.py`

## Test plan (tests first; write, watch fail, then pass)
Field mapping from fixture receipts; banned-words test (neutral, offset, carbon-free).

## Acceptance criteria
One export from the real receipts, linked from the receipt view.
- `make test` green, `ruff`/`tsc` clean, and the branch pushed.

## Dependencies
M07

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
