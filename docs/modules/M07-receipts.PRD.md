# M07 PRD: Receipt spec, JSON schema, SHA-256, Sigstore and Rekor
Depends on: M06, schemas · Estimate: 6 h · Contracts: `schemas/SCHEMAS.md`

## Why
Turn each result into a small, signed, public, checkable file. Numbers only, because Rekor is public and permanent.

## Requirements
- FR-RCPT-01 Build receipts that validate against `schemas/receipt.schema.json`.
- FR-RCPT-02 Canonicalize with JCS (RFC 8785), SHA-256 over the receipt minus `integrity`; chain with `prev_sha256`.
- FR-RCPT-03 Sign with Sigstore (keyless, GitHub OIDC as `ak9381`), upload to Rekor, store the bundle next to the receipt. the maintainer is told before the first public signing.
- FR-RCPT-04 Offline mode (`--no-rekor`): sign with a local key for tests; grade can then be at most ESTIMATE.
- FR-RCPT-05 A linter rejects any receipt with prompt/output text fields or strings longer than 200 chars outside `planner_trace.reason`.
- FR-RCPT-06 Grades: VALID / ESTIMATE / NOISE / UNVERIFIABLE as defined in SCHEMAS.md.
- FR-RCPT-07 Publish `SPEC.md` (open receipt spec) and one outside-format adapter (Neuralwatt-style energy JSON → graded UNVERIFIABLE).

## Out of scope
Payer attestation, blockchain anything.

## Done means
Three real receipts (container saving, Pareto best, re-run) signed and in Rekor; three forged fixtures for the verifier.

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
