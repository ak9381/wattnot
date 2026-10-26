# M09 PRD: Run event stream and API
Depends on: schemas · Estimate: 4 h · Contracts: `schemas/SCHEMAS.md`

## Why
One event log per run that the CLI writes and the frontend reads, live or replayed. This is the contract between backend and UI.

## Requirements
- FR-EVT-01 Emitter writes `runs/<run_id>/events.jsonl` (append, fsync per event) validated against `run-event.schema.json`.
- FR-EVT-02 FastAPI: `GET /api/runs`, `GET /api/runs/{id}`, `GET /api/runs/{id}/events` (SSE, resumable with Last-Event-ID), `GET /api/receipts/{id}`, `GET /api/pareto/{run_id}`.
- FR-EVT-03 Replay mode: `?replay=1&speed=4` streams a finished run at 4× so the demo never depends on a live GPU.
- FR-EVT-04 Read-only API. No endpoint starts GPU work (runs start from the CLI only).
- FR-EVT-05 Max 2 demo servers: API + static frontend; nothing else listens.

## Out of scope
Auth, multi-user, a database (files are enough).

## Done means
Frontend renders the fixture run identically in live and replay mode.

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
