# WattNot receipt spec, version 0.1 (open draft)

Status: **draft, comments welcome.** This is how WattNot writes receipts today. It is not a
standard, nobody has adopted it, and it will change. If you build on it, pin the version.

## What a receipt is

One small JSON file that says what a serving change did to energy, cost and quality, plus
enough hashes and a signature that anyone can check the file was not edited. It holds
numbers, ids and settings. It never holds prompts or model outputs.

The base format is `wattnot.receipt/1` (see `SPEC.md` and `schemas/receipt.schema.json`).
This draft adds one derived block, three receipt profiles and a one-file verifier.

## 1. Joules per answer

Every receipt may carry a `joules` block. It is derived, never typed in by hand:

```
j_per_answer      = wh_per_req x 3600
j_per_good_answer = j_per_answer / pass_rate
```

- `wh_per_req` comes from the arm (`baseline` or `candidate`).
- `pass_rate` is `quality.fixed_pass_rate`: the share of answers (0 to 1) that passed the
  quality gate.
- If `pass_rate` is 0 or missing there is no good answer, and `j_per_good_answer` is `null`.
- If the arm's `wh_per_req` has a `range` (`low`, `high`), the range is carried through as
  `j_per_answer_range` and `j_per_good_answer_range`.
- Numbers are rounded to 6 decimals. Verifiers compare with a tolerance of 1e-6.

Why divide by the pass rate: a change that is cheap because it answers worse should look worse,
not better. Joules per good answer is the number to compare two setups on.

```json
"joules": {
  "unit": "J",
  "label": "ESTIMATE",
  "formula": "j_per_answer = wh_per_req x 3600; j_per_good_answer = j_per_answer / pass_rate",
  "baseline":  {"j_per_answer": 172.9, "pass_rate": 0.95, "j_per_good_answer": 182.0},
  "candidate": {"j_per_answer": 155.6, "pass_rate": 0.95, "j_per_good_answer": 163.8},
  "change_pct": {"j_per_answer": -10.0, "j_per_good_answer": -10.0}
}
```

`label` copies `measurement.label`. The block sits inside the hash. A verifier re-derives it
from the arms, and a receipt whose block disagrees with its own numbers is graded
`UNVERIFIABLE`, even if the hash and signature are fine.

## 2. Labels

Every number is one of:

| Label | Meaning |
|---|---|
| MEASURED | read from a meter or an API (NVML energy counter, token counts in a usage block) |
| ESTIMATE | computed from stated assumptions, shown as a range where it can be |
| SIMULATED | produced by a model or a replay of made-up data. Never shown as real |
| MOCK | a document that looks official but is not (see the certificate mock) |

Water is always ESTIMATE. Money is US dollars from a stated tariff or price list.

## 3. Profiles

`profile` is optional. No profile means a GPU receipt.

| Profile | Use | Welch test | Best possible grade |
|---|---|---|---|
| (none) / `gpu` | measured GPU runs, repeated | yes | VALID |
| `cloud` | a hosted model: tokens and USD from the API, energy from a FLOP model | no | ESTIMATE |
| `flex` | one grid-shed event on a GPU (see `wattnot flex`) | no | ESTIMATE |

A receipt with no significance test has `result.welch.applicable = false`. It skips the NOISE
check and can never be VALID: one run is not enough evidence for the top grade.

### The cloud adapter

`wattnot receipt from-cloud <cloud receipt>` turns a `wattnot.cloud-receipt/1` file into a
receipt of this spec. Baseline is the same requests at `xhigh` (the API default), candidate is
the tuned effort. Energy is the middle of an ESTIMATE range, with the range kept. The original
receipt is pinned by sha256 in the `cloud` block. It is a new document and needs its own
signature.

## 4. Hash, chain, signature

1. Drop the `integrity` block.
2. Canonicalize with JCS (RFC 8785).
3. SHA-256 the UTF-8 bytes: that is `integrity.sha256`.
4. `integrity.prev_sha256` links to the previous receipt in a chain.
5. An Ed25519 signature over those 32 bytes lives in a bundle next to the receipt
   (`<name>.sigstore.json`). Offline bundles have no transparency-log entry, so their grade
   stops at ESTIMATE. Public Rekor signing is permanent and is off unless the project owner
   switches it on.

## 5. Grades

| Grade | Meaning |
|---|---|
| VALID | MEASURED, tested, hash and signature OK, and a Rekor entry exists |
| ESTIMATE | integrity OK, but the label is ESTIMATE, there is no Rekor entry, or there is no significance test |
| SIMULATED | integrity OK, but the numbers come from a simulation or a made-up fixture |
| NOISE | integrity OK, but Welch p is at or above alpha: the saving is not billed |
| UNVERIFIABLE | broken hash or signature, unknown signer, wrong spec, or a derived block that does not match |

The grade a receipt claims is a hint. A verifier always re-derives it.

## 6. The verifier

`verifier/wattnot_verify.py` is one file, Python standard library only (it includes a small
pure-Python Ed25519 check), so you can read it end to end and run it anywhere:

```
python verifier/wattnot_verify.py receipt.json
python verifier/wattnot_verify.py receipt.json --json
```

It prints the steps `schema`, `sha256`, `signature`, `rekor`, `joules` (only if the receipt
carries the block) and `grade`. Exit code 1 means UNVERIFIABLE. `wattnot verify` is the same
check with the full JSON Schema.

Known difference: the one-file verifier checks the structure (required fields, hash format,
label and grade words), not the whole JSON Schema. On the conformance vectors the two agree
step for step. Please report any receipt where they do not.

## 7. Conformance vectors

`verifier/vectors/` holds nine receipts with their bundles and `expected.json`:

| Vector | Grade | What it shows |
|---|---|---|
| real-measured | ESTIMATE | a MEASURED run, signed offline (no Rekor, so not VALID) |
| real-estimate | ESTIMATE | an ESTIMATE label |
| real-noise | NOISE | the saving is inside the noise |
| real-joules | ESTIMATE | carries a correct `joules` block |
| real-cloud | SIMULATED | a hosted-model receipt from a made-up fixture |
| forged-edited | UNVERIFIABLE | a number edited after signing |
| forged-rehashed | UNVERIFIABLE | edited and re-hashed, old signature kept |
| forged-otherkey | UNVERIFIABLE | re-signed by another key, still naming the first signer |
| forged-joules | UNVERIFIABLE | `joules` edited, hash and signature re-made, block no longer matches |

All signatures use a public test key built from a fixed seed. They prove nothing about any
real run. `python verifier/vectors/make.py` rebuilds them.

## 8. What this does not claim

- Nobody besides us uses this. There is no standards body and no review yet.
- A hosted-model energy figure is an estimate from token counts, not a reading from the
  provider's hardware.
- Hash and signature prove a file was not changed after signing. They do not prove the run
  was honest. That is what the MEASURED label, the CSV hashes and the quality gate are for.
