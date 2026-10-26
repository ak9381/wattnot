# M09 TDD: Run event stream and API
Branch: `feat/m09-events` · Hours: 4 · Depends on: schemas

## Interfaces
SSE frame: `id: <seq>\nevent: <type>\ndata: <event json>\n\n`
`GET /api/runs` → `[{run_id, started_at, mode, model, status, best_saving_pct}]`

## Data schemas
Uses the canonical run event (`schemas/run-event.schema.json`) and receipt (`schemas/receipt.schema.json`). Module-local formats are defined under Interfaces. Never add fields to the canonical schemas inside this module; open a schema task instead.

## File layout (this module owns only these paths)
`wattnot/events/{emitter,schema}.py`, `wattnot/api/app.py`, `tests/test_events.py`, `tests/fixtures/runs/demo_run/events.jsonl`

## Test plan (tests first; write, watch fail, then pass)
Schema validation of every emitted event; seq strictly increasing; SSE resume from Last-Event-ID; replay speed.

## Acceptance criteria
Frontend renders the fixture run identically in live and replay mode.
- `make test` green, `ruff`/`tsc` clean, and the branch pushed.

## Dependencies
schemas

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.


## Change (Oct 26)
- `run.started` `data.compare_to` (optional): run_id of the baseline run. Must match the run_id pattern or be null.
- Test: old streams without it still validate.

## Additive change (Oct 26): fetch a run by ID (for compare_to)
- `GET /api/runs/{run_id}` → `{run_id, summary: <same object as GET /api/runs item>, events: [<run event>…], receipt: <receipt> | null}`. 404 `{code:"run_not_found"}` for unknown id; 400 if `run_id` fails the `run_…` pattern. Events read from `runs/<run_id>/events.jsonl` (disk is truth), in `seq` order.
- `GET /api/runs/{run_id}/events` → SSE replay of the same log using the frame above; honours `Last-Event-ID` (resume after that seq) and `?speed=` (replay pacing, default 1, `0` = as fast as possible). Live runs keep streaming; finished runs close after `run.finished`.
- Frontend uses these when `compare_to` is set (receipt top-level or `run.started` `data.compare_to`); if the baseline run 404s it falls back to rebuilding from receipt `baseline`.
- Tests: fixture `demo_run` + a baseline fixture → `GET /api/runs/{id}` events validate against run-event schema and receipt against receipt schema; events identical (byte-for-byte JSON) to SSE replay frames; unknown id → 404, bad id → 400; resume from Last-Event-ID yields only seq > N.

## Progress (T06)
- Done: event emitter + JSONL writer (`wattnot/events/{emitter,schema}.py`), schema check on every event before write, seq strictly increasing from 0, fsync per line, reopen-and-append; 22 tests in `tests/test_events.py`, all green with the demo fixture.
- Not done: the FastAPI read-only API and SSE/replay (that is T26).
- Next: T26 builds `wattnot/api/app.py` on this emitter and the committed fixture.

## Progress (T26)
- Done: read-only FastAPI (`wattnot/api/app.py`) — `GET /api/runs`, `/api/runs/{id}`, `/api/runs/{id}/events` (SSE replay, `Last-Event-ID` resume, `?speed=` pacing, closes after `run.finished`), `/api/receipts/{id}`, `/api/pareto/{id}`; SSE `data` frames are byte-for-byte the events the JSON view returns; run lookup resolves by the `run_id` in the log so the `demo_run` fixture works. fastapi/uvicorn added as deps, httpx as dev dep. New API tests in `tests/test_events.py` via `TestClient` (no socket); `make lint`/`make test` green (65 tests).
- Not done: live file tailing (current stream replays the recorded log and closes); this read-only surface never starts GPU work.
- Next: frontend consumes these to render the fixture run in live and replay mode.
