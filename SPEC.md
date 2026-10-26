# WattNot receipt spec (`wattnot.receipt/1`)

A receipt is one small JSON file that says how much energy a serving change
saved per request, and lets anyone check it. It holds numbers, ids and
settings only. No prompts, no model outputs.

The full field list and JSON Schema are in `schemas/receipt.schema.json`
and `schemas/SCHEMAS.md`. The short version:

- `subject`: model, host, mode, workload (with the replay set's sha256).
- `baseline` / `candidate`: params, reps, `wh_per_req` and `usd_per_req`
  (mean, sd, per-rep samples), `p99_ms`, `quality`.
- `result`: `saving_pct` {mean, ci_low, ci_high}, `welch` {t, df, p_value,
  alpha}, `billable`, `billed_saving_pct` (the CI lower bound, or 0),
  `fee_share`. Money is US dollars from a stated tariff (an ESTIMATE).
- `measurement`: meter, Hz, CSV links with hashes, and a MEASURED or
  ESTIMATE label.
- `planner_trace`: every decision (source, model, params, reason, tokens).
- `estimates`: water per request, before and after. Always ESTIMATE.
- `grade` and `integrity`.

## Hash

1. Drop the `integrity` block.
2. Canonicalize with JCS (RFC 8785).
3. SHA-256 the UTF-8 bytes. That is `integrity.sha256`.

`integrity.prev_sha256` links each receipt to the one before it, so a
removed or reordered receipt shows up.

## Grades

| Grade | Meaning |
|---|---|
| VALID | MEASURED, hash matches, signature OK, Rekor entry found |
| ESTIMATE | Integrity OK, but the meter label is ESTIMATE or there is no Rekor entry |
| NOISE | Integrity OK, but Welch p >= 0.05, so nothing is billed |
| UNVERIFIABLE | No hash, no signature, or not this spec (for example a plain energy JSON from another tool) |

## Lint

A receipt is rejected if it has a prompt or output text field anywhere, or
any string over 200 characters (planner reasons may be up to 600).
