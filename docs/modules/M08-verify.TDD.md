# M08 TDD: `wattnot verify` CLI and WASM browser verifier
Branch: `feat/m08-verify` · Hours: 10 · Depends on: M07

## Interfaces
WASM export: `verify(receipt_json: string, bundle_json?: string) -> VerifyReport`
```json
{"grade":"VALID","steps":[{"name":"schema","ok":true},{"name":"sha256","ok":true,"expected":"…","got":"…"},{"name":"signature","ok":true,"signer":"ak9381"},{"name":"rekor","ok":true,"log_index":123}]}
```

## Data schemas
Uses the canonical run event (`schemas/run-event.schema.json`) and receipt (`schemas/receipt.schema.json`). Module-local formats are defined under Interfaces. Never add fields to the canonical schemas inside this module; open a schema task instead.

## File layout (this module owns only these paths)
`verifier/core/` (Rust crate), `verifier/web/` (static page), `wattnot/cli/verify.py` (calls the same core via wasmtime or a Python port with shared test vectors), `verifier/samples/`

## Test plan (tests first; write, watch fail, then pass)
Shared golden vectors: 6 samples × expected step results, run in both CLI and WASM (headless browser test with Playwright).

## Acceptance criteria
Public URL live by Oct 30; the forge challenge recorded (opens the video).
- `make test` green, `ruff`/`tsc` clean, and the branch pushed.

## Dependencies
M07

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
