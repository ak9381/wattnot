# M08 PRD: `wattnot verify` CLI and WASM browser verifier
Depends on: M07 · Estimate: 10 h · Contracts: `schemas/SCHEMAS.md`

## Why
Anyone can drop a receipt into a public web page and watch it get checked step by step, with no account and no server. A Neuralwatt-style energy JSON shows UNVERIFIABLE next to ours showing VALID.

## Requirements
- FR-VER-01 CLI: `wattnot verify receipt.json [--bundle] [--meter zeus|dcgm --rerun]` prints each step and the grade.
- FR-VER-02 Browser: drag-and-drop; steps shown one by one: schema → JCS + SHA-256 recompute → signature check → Rekor lookup (public API, client-side fetch) → label → grade.
- FR-VER-03 Same verification core compiled to WASM (Rust or Go; pick one on day 1, Rust preferred) so CLI and browser give identical results.
- FR-VER-04 Ships 3 real + 3 forged samples (edited number → FAIL at SHA-256; stripped signature → UNVERIFIABLE; Neuralwatt-style JSON → UNVERIFIABLE).
- FR-VER-05 Static hosting (GitHub Pages or similar) at a public URL; works offline except the Rekor step.
- FR-VER-06 `--rerun`: re-run the workload on the user's own meter and compare Wh within the receipt's CI.

## Out of scope
Accounts, uploads to our server, storing anything the user drops.

## Done means
Public URL live by Oct 30; the forge challenge recorded (opens the video).

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
