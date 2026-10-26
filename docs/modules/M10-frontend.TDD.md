# M10 TDD: Frontend: run canvas, Receipt+Verifier, Pareto+Planner, Rack/Floor map
Branch: `feat/m10-frontend` · Hours: 9 · Depends on: M09, M08 (WASM), designs from UI UX Designer bot

## Interfaces
Stack: Vite + React + TypeScript, React Flow (@xyflow/react), a chart lib (Recharts or visx, pick day 1), Tailwind. Types generated from the JSON schemas (`json-schema-to-typescript`). Data via `EventSource` or fixture JSONL.

## Data schemas
Uses the canonical run event (`schemas/run-event.schema.json`) and receipt (`schemas/receipt.schema.json`). Module-local formats are defined under Interfaces. Never add fields to the canonical schemas inside this module; open a schema task instead.

## File layout (this module owns only these paths)
`web/src/{canvas,receipt,pareto,rack}/`, `web/src/types/` (generated), `web/public/fixtures/`, `web/tests/` (Playwright)

## Test plan (tests first; write, watch fail, then pass)
Playwright: load fixture run → canvas has expected node count and statuses; drop forged receipt → FAIL at sha256; Pareto renders front; fallback banner shows when `sam.status=cut`.

## Acceptance criteria
All 4 views work on the fixture run and on one real run; screenshot set for the video.
- `make test` green, `ruff`/`tsc` clean, and the branch pushed.

## Dependencies
M09, M08 (WASM), designs from UI UX Designer bot

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.


## Change (Oct 26)
- field-map.js: add `estimates.baseline_water_ml_per_req` and `compare_to` (receipt) / `data.compare_to` (run.started).
- Before canvas: fetch `compare_to` run if present, else rebuild from receipt baseline. Before water shows ESTIMATE badge always; derive if field absent.

## Additive change (Oct 26): compare_to fetch + UI view schema
- Before canvas: if `compare_to` set, `GET /api/runs/{compare_to}` (events + receipt), or replay via `GET /api/runs/{compare_to}/events`; on 404 rebuild from receipt `baseline`.
- Derived UI blocks follow `schemas/ui-view.schema.json` (`matrix_cards[]`, `planner_reasons[]`). Built client-side in `web/src/types/` selectors from events + receipts; never signed, never in the receipt hash, never sent back to the API.
- Test: Vitest/Playwright validate the derived view of the fixture run against ui-view.schema.json (ajv); matrix row count = tiers in fixture.
