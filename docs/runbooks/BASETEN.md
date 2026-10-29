# Cloud mode on Baseten

WattNot's GPU mode needs a box you control. Cloud mode works on a hosted model where you
only get an API: Baseten's Model API for Muse Spark Open (`meta-models/muse-spark-open`).
You can't change the batch size or the power cap there. You can change how hard the model
thinks, how long it may think, and how your prompt is laid out. This runbook covers those.

Labels used everywhere: **MEASURED** (from the API's usage block and our clock), **ESTIMATE**
(derived), **ADVICE** (a suggestion, not a result), **SIMULATED** (made-up fixture, no API
call). Money is USD.

## What it does

1. **Effort router.** Muse Spark Open always reasons. `reasoning_effort` is `low`, `medium`,
   `high` or `xhigh` (the default). Reasoning tokens are billed as output, so effort is the
   biggest cost lever. For each prompt class we run `xhigh` as the reference, then try cheaper
   levels. A level is kept only if it passes:
   - the fixed answer checks (same pass rate as `xhigh`, within `--max-drop-pts`, default 1),
   - the p99 gate (p99 no worse than `xhigh` p99 plus 10%), and
   - optionally the model judge (`--judge`).

   Each level gets its own `max_tokens` (it caps thinking plus answer) and stop sequences. A
   reply with `finish_reason: length` or an empty answer counts as a failed answer. If a cheap
   answer fails its check we count a retry at `xhigh` in both latency and cost, so the saving
   is net of the escape hatch. Muse Spark proposes the order to try. It only proposes. The gates
   decide, and if its answer is broken we use `low, medium, high`.
2. **Receipt with a baseline.** Per class: tokens (prompt, cached, completion, reasoning when
   the API gives it), USD and latency, plus what the same requests would have cost at
   `xhigh`. It also shows what the tuning run itself cost and how many workload runs it
   takes to pay that back. Energy is an ESTIMATE range from FLOPs.
3. **Audit.** `wattnot cloud audit` (default `--source per-call`) reconciles the receipt with the
   usage blocks, ids and headers Baseten returned in the recorded replies, and recomputes cost
   from the list prices in `GET /v1/model_apis`. It is reconciled against Baseten's per-response
   usage, not the billing meter. `--source meter` compares with the workspace usage endpoint
   instead, which a managed hackathon key cannot read.
4. **Cache knobs.** Stable-prefix prompt layout, the `x-session-affinity` header, the cached
   share, an A/B (`cache-ab`) and a cheap half-life probe (`probe`).

## Try it with no key (SIMULATED)

```bash
pip install -e .
wattnot cloud demo --out cloud-demo
```

This replays `data/cloud/demo-fixture.json`, a made-up fixture (see
`scripts/gen_cloud_fixture.py`). It makes no network call and every number is labelled
SIMULATED. It writes a receipt, the call log, the meter audit and an HTML page. One rendered copy is
committed in `reports/cloud-demo/`. Use it to see the flow, not to quote savings.

## First live run

The key comes only from the environment. There is no flag and no file for it.

```bash
export BASETEN_METRICS_KEY=...               # in your shell; never commit it
# BASETEN_API_KEY is read only if BASETEN_METRICS_KEY is unset (it returned 401 on 29 Oct 2026)

# 1. Smoke test: two classes, one repeat, small cap. Records replies for replay.
wattnot cloud tune --live --classes extract,arith --repeats 1 --budget-usd 1 \
  --record data/cloud/recorded-smoke.json --out cloud-receipts

# 2. The real run (about 120 calls). The 80% stop line keeps it under the budget.
wattnot cloud tune --live --budget-usd 10 \
  --record data/cloud/recorded-run1.json --out cloud-receipts

# 3. Reconcile with Baseten's per-response usage (offline; needs the recording from step 2)
wattnot cloud audit cloud-receipts/<receipt_id>.json --recording data/cloud/recorded-run1.json
#    optional: wattnot cloud audit <receipt> --source meter (needs usage-read access)

# 4. Cache: layout A/B and the half-life probe (the probe waits up to 10 minutes)
wattnot cloud cache-ab --live
wattnot cloud probe --live

# 5. Show or re-render any receipt
wattnot cloud show cloud-receipts/<receipt_id>.json
wattnot cloud report cloud-receipts/<receipt_id>.json
```

Spend, as an ESTIMATE: a call is capped at the answer budget plus the level's allowance
(1.5k, 4k, 12k, 32k thinking tokens for low to xhigh). The hard ceiling for step 2 is
about 120 calls at the `xhigh` cap, near $16 at $4.25 per 1M output tokens; real runs
finish far below that, and `--budget-usd` stops new levels at 80% of what you set.

Replay a recorded run later, offline, as MEASURED:

```bash
wattnot cloud tune --fixture data/cloud/recorded-run1.json --out cloud-receipts
```

Keep the same `--classes`, `--repeats` and `--efforts` as the recording. A call that is not
in the fixture is an error, never a guess. Recorded fixtures keep the answer text (so checks
can be re-run) but not the reasoning text, only its length and hash.

If you edit the workload, re-run the live step. The workload file is hashed into the receipt.

## What you can open

- `cloud-receipts/<id>.json`: the receipt. Hash-chained (`integrity.prev_sha256`), JCS
  canonical sha256, no prompt or answer text. `--sign-key` adds an offline signature.
- `cloud-receipts/<id>.calls.jsonl`: one line per API call, numbers and hashes only.
- `cloud-receipts/<id>.html`: the receipt page.
- `cloud-receipts/<id>.audit.json`: the meter audit, a sidecar so the receipt hash stays put.
- API: `GET /api/cloud/receipts` and `GET /api/cloud/receipts/{id}?format=html` on the
  read-only API (root holds `cloud-receipts/`).

## What the first live runs showed (2026-10-27, MEASURED)

Receipts and recordings: `reports/cloud-live/`, `data/cloud/recorded-*.json`.

| Item | Result |
|---|---|
| Prices ($1.25 input, $0.15 cached, $4.25 output per 1M) | Still the published rates. The usage and billing endpoints refuse this key (403), so nothing was checked against an invoice. Receipts keep saying so. |
| `usage.completion_tokens_details.reasoning_tokens` | Present on every call, and it is inside `completion_tokens` (reasoning plus answer). |
| `usage.prompt_tokens_details.cached_tokens` | Present. It moves in blocks of 128 tokens, so a prompt under 256 tokens caches 0 or 128. |
| Usage endpoint | `GET /v1/model_apis/usage` and `/v1/billing/model_apis` answer 403 `PERMISSION_DENIED`: this key is inference-only. `audit` prints NOT_AVAILABLE. Set `BASETEN_METRICS_KEY` to a metrics-scoped key to run it. |
| Rate limits | The response headers say 100 requests per minute and 2M tokens per minute on this key. The transport still spaces calls 4 s apart. |
| Cache lifetime | A 2.2k-token prefix was still 98.7% cached after 10 minutes. |
| Judge | The model judge returned parseable verdicts on all 8 calls. |
| Planner | At `low` the model spent about 1.1k to 1.5k reasoning tokens on the order proposal, so the old 1500 cap truncated it. The cap is now 6000. |
| Runaway `xhigh` | One `xhigh` call used all 32k tokens and hit the length cap (cost $0.136, 46 s). Reference calls can be heavy tailed; use repeats of 5 or more before quoting a saving. |
| Energy | Still an ESTIMATE. We can't see Baseten's GPUs. |

The meter audit does not read Prometheus (`baseten_model_api_tokens_total`); that path is
not built.

## Reading the result

- A class that stays on `xhigh` is a finding, not a failure: no cheaper level passed the gates.
- Compare `tuned_usd` with `would_have_cost_xhigh_usd`, then check `experiment_usd` and
  `payback_runs`. A saving that needs 200 runs to pay back is not worth it for a one-off.
- The workload has 15 prompts in 5 classes. That's enough to see the shape, not to promise it
  on your traffic. Point `--workload` at your own JSONL (same format as `workloads/`) before
  you act on a number.
- Quality is judged by exact checks against gold answers plus agreement with `xhigh`. Add
  `--judge` to add a model-graded check.

## Troubleshooting

- `BASETEN_API_KEY is not set`: export it in the same shell.
- `refusing to send model ...`: the transport only sends `meta-models/muse-spark-open`.
- Many `length` finishes at `low`: that level truncates on this class. The router drops it.
- Stopped early at 80% of budget: the receipt records it (`stopped`); raise `--budget-usd`.
