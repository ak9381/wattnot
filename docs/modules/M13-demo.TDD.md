# M13 TDD: Demo and video script support
Branch: `feat/m13-demo` · Hours: 6 · Depends on: all

## Interfaces
`make demo`, `make forge-demo`, `docs/VIDEO.md` (script + timings), `docs/REAL-VS-SIM.md`

## Data schemas
Uses the canonical run event (`schemas/run-event.schema.json`) and receipt (`schemas/receipt.schema.json`). Module-local formats are defined under Interfaces. Never add fields to the canonical schemas inside this module; open a schema task instead.

## File layout (this module owns only these paths)
`Makefile`, `scripts/demo.sh`, `docs/VIDEO.md`, `docs/REAL-VS-SIM.md`, `README.md`

## Test plan (tests first; write, watch fail, then pass)
`make demo` runs headless on fixtures in CI (Playwright screenshot of each view).

## Acceptance criteria
- `docs/VIDEO.md` opens with the prior-work line, then who benefits and by how much, then the problem statement. No chat-style screens in the shot list.
Two clean takes recorded by Nov 4; README links verifier URL, Rekor entries and CSVs.
- `make test` green, `ruff`/`tsc` clean, and the branch pushed.

## Dependencies
all

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
