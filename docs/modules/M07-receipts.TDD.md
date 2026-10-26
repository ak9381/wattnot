# M07 TDD: Receipt spec, JSON schema, SHA-256, Sigstore and Rekor
Branch: `feat/m07-receipts` · Hours: 6 · Depends on: M06, schemas

## Interfaces
```
wattnot receipt build --run runs/<id> --out receipts/
wattnot receipt sign receipts/<id>.json [--no-rekor]
```
Python: `build_receipt(run) -> dict`, `canonical_sha256(d) -> str`, `sign(path) -> BundlePath` (sigstore-python).

## Data schemas
Uses the canonical run event (`schemas/run-event.schema.json`) and receipt (`schemas/receipt.schema.json`). Module-local formats are defined under Interfaces. Never add fields to the canonical schemas inside this module; open a schema task instead.

## File layout (this module owns only these paths)
`wattnot/receipt/{build,canon,sign,lint,grade}.py`, `SPEC.md`, `tests/test_receipt.py`, `tests/fixtures/receipts/{valid,estimate,noise}.json`

## Test plan (tests first; write, watch fail, then pass)
JCS vectors from RFC 8785; editing one digit changes the hash; lint catches a `prompt` field; offline signing round trip; schema validation of fixtures.

## Acceptance criteria
Three real receipts (container saving, Pareto best, re-run) signed and in Rekor; three forged fixtures for the verifier.
- `make test` green, `ruff`/`tsc` clean, and the branch pushed.

## Dependencies
M06, schemas

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.


## Change (Oct 26)
- Write optional `estimates.baseline_water_ml_per_req` {value, label:"ESTIMATE", method, source} with the same method as `water_ml_per_req`.
- Write optional top-level `compare_to` = baseline run_id when a baseline run exists. Both fields are inside the JCS hash.
- Tests: receipt without either field still validates; label other than ESTIMATE fails.
