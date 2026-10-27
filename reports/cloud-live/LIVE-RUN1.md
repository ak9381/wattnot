# Baseten live run 1: Muse Spark Open, 2026-10-27

Everything here is **MEASURED** unless marked. Model `meta-models/muse-spark-open-1026` on
Baseten's Model API, USD, published rates ($1.25 input, $0.15 cached, $4.25 output per 1M tokens).
Code and receipts: branch `feat/cloud-live` in ak9381/wattnot (`reports/cloud-live/`,
`data/cloud/recorded-*.json`). No prompt is confidential: the 15-prompt workload is public.

## Spend

About **$0.81** of the $11 allowance. Smoke $0.027, run 1 $0.301, run 2 $0.416, cache A/B $0.058,
cache probe about $0.008 (the numbers are the sum of per-call USD from the usage block).
Not checked against an invoice (see the meter audit below).

## Effort router vs always-xhigh

Run 2 is the one to quote: 5 repeats, 15 prompts, 5 classes, model judge on, 75 requests per level.

| class | chosen | tuned USD | xhigh USD | saving | quality (chosen / xhigh) | judge worse |
|---|---|---|---|---|---|---|
| extract | low | 0.0199 | 0.0390 | 48.9% | 100% / 100% | 0 of 15 |
| classify | low | 0.0141 | 0.0249 | 43.5% | 100% / 100% | 0 of 15 |
| trace | low | 0.0141 | 0.0273 | 48.5% | 100% / 100% | 0 of 15 |
| arith | low | 0.0174 | 0.0333 | 47.8% | 100% / 100% | 0 of 15 |
| logic | low | 0.0167 | 0.0456 | 63.5% | 100% / 100% | 0 of 15 |
| **all** | | **0.0821** | **0.1701** | **51.7%** | | |

- Completion tokens 17,118 vs 37,778. Escape retries: 0. p99 gate passed on every level kept.
- Saving net of the tuning run itself: the run cost $0.416, so it pays back in 23.7 workload runs.
- Run 1 (2 repeats, no judge) reported 83.0%, but one `xhigh` call (extract-2) ran all
  32,037 reasoning tokens and hit the length cap (cost $0.136, 46 s). Without it the saving
  is about 53%. That one call made the xhigh baseline for `extract` score 83% on quality.
  Do not quote 83%. Quote 52%.
- The smoke run (6 requests) gave 48.6%.
- Only 15 prompts in 5 easy-to-medium classes, all answered correctly at `low`. This shows the
  shape. It does not promise the same on your traffic.

Surprise: `high` and `medium` are not cheaper than `xhigh` here. Per request, logic costs
$0.00368 at high, $0.00304 at medium and $0.00304 at xhigh; arith costs $0.00244 at medium
and $0.00222 at xhigh. Only `low` is reliably cheaper. The router still keeps the cheapest
level that passes, so it lands on `low` for all five classes.

## The unknowns, now checked

| Item | Result |
|---|---|
| Rates | Not confirmed. The billing endpoints refuse this key. |
| `reasoning_tokens` | Present on every call, inside `completion_tokens`. |
| `cached_tokens` | Present, in blocks of 128 tokens. A prompt under 256 tokens caches 0 or 128. |
| Usage endpoint | 403 `PERMISSION_DENIED` on `/v1/model_apis/usage` and `/v1/billing/model_apis`: the key is inference-only. **Meter audit: NOT_AVAILABLE**, no match figure. |
| Rate limit | Headers: 100 requests/min, 2M tokens/min. We still space calls 4 s apart. No 429 seen. |
| Judge format | Parsed fine on all 8 judge calls. |
| Planner | At `low` it used 1.1k to 1.5k reasoning tokens on a tiny JSON task. The old 1500 cap cut it off in the smoke run, so it fell back to `low, medium, high`. Fixed with a 6000 cap; in runs 1 and 2 it proposed orders (medium first for arith, high first for logic). |

## Meter audit retries (2026-10-27)

**Try 1** (metrics variable held the inference key): 403 `PERMISSION_DENIED`.

**Try 2** (new metrics-scoped key from a file; different key from the inference key): the key
works, but the meter has nothing. Still **NOT_AVAILABLE**, no match percentage.

- `GET /v1/model_apis/usage` answered 200 for all three live receipts, with 1-minute buckets
  filtered to the inference key's prefix, and with no filter, 1-hour and 1-day buckets for the
  whole day, grouped by key, model, user and team. Every bucket had an empty `results` list.
- `GET /v1/billing/model_apis` answered 200 and listed no cost for any day from Oct 20 to 27.
- So the metrics key is probably in a different workspace from the one that served the calls
  (the hackathon key), or the hackathon traffic is not billed to this workspace. Meter lag is
  unlikely: the workspace shows no usage at all for the day, and the audit ran 30 minutes after
  the last call. This is a guess; Baseten would have to confirm.
- Our receipts hold 296 requests across the three live receipts (13, 79 and 204) and
  about 234k tokens; the meter holds none, so there is nothing to compare.
- First version of the audit printed this as a 100% mismatch. That was misleading, so an empty
  meter is now reported as NOT_AVAILABLE with the reason above (test added).

**Credit balance check (read-only, metrics key).** There is no balance endpoint in the
Management API. `GET /v1/billing/usage_summary` (Oct 1 to 29) returned $0.00 subtotal,
$0.00 credits used and $0.00 total for dedicated, training and Model APIs. `GET /v1/organizations/me`
shows the metrics key's workspace is "Wattnot", created 2026-10-27 04:45 UTC, a few minutes
before this check and after all our live calls. That is why it shows nothing: the inference key
that made the calls was issued in a different account (the hackathon credits), so this workspace
cannot see its usage, credits or balance. Credits remaining and used-so-far in USD are unknown;
the only spend figure is our own receipts, about $0.81.

**Price check, from `GET /v1/model_apis`** (the key can read the catalog):

| | Catalog | Our receipts | Result |
|---|---|---|---|
| Input per 1M | $1.25 | $1.25 | matches |
| Output per 1M | $4.25 | $4.25 | matches |
| Cached input per 1M | not listed | $0.15 | unconfirmed |

The catalog names the model `meta-models/muse-spark-open` (no `-1026` suffix), shows a 120
requests/min and 500k tokens/min limit for it, while our key's response headers said 100 and 2M.
Prices are confirmed for input and output only. They are list prices from the catalog, not a bill.
To finish the audit: use a metrics-scoped key created in the same workspace as the hackathon
key (or ask Baseten whether hackathon usage is exposed at all), then run `wattnot cloud audit`
on the three receipts in `reports/cloud-live/`.

## Cache

- Half-life probe: a 2,204-token prefix read back 98.7% cached (2,176 tokens) at 10, 30, 60,
  180 and 600 s. Still warm at 10 minutes, so the half-life is above that.
- Layout A/B, 8 calls per arm, one 2.2k-token shared document:

| arm | cached share | cached share after 1st call | input-only USD | input saving |
|---|---|---|---|---|
| volatile-first | 0.0% | 0.0% | 0.0222 | baseline |
| stable-first | 86.2% | 98.6% | 0.0053 | 76.1% |
| stable-first + affinity header | 86.1% | 98.4% | 0.0054 | 75.9% |

  Putting the stable text first is the whole win. The affinity header made no measurable
  difference on this account. The total-cost figures the command printed first (59.9% and 52.5%)
  also include output length, which varies call to call, so use the input-only column.

## Code fixes found by the live run (with tests)

1. No pacing: the transport now starts calls at least 4 s apart.
2. Failed calls did not stop a run: a 4xx (not 408 or 429) now stops it at once, and three
   failures in a row stop it too. The recording is saved on a stop.
3. Planner cap 1500 truncated its answer: now 6000.
4. Usage 403 gave a bare status: it now names the reason and the fix. `BASETEN_METRICS_KEY`
   (metrics-scoped key) can be used just for the audit.
5. Cache A/B printed output-length noise as a caching saving: it now reports input-only USD too.

## Not done

Meter audit match (needs a real metrics-scoped key, see above), invoice check of the rates, Prometheus path.
Energy stays an ESTIMATE: 0.87 to 8.73 Wh avoided in run 2, from FLOPs, not measured.
