# Meta API usage and the $50 spend cap

WattNot's agent decisions run on Muse Spark through the Meta Model API. Every call goes through a
spend guard, and total spend is capped at **$50 USD** (raised from $20 on 2026-10-28).

## Price used

`muse-spark-1.3`, Standard mode, per 1M tokens: **$1.25 input, $0.15 cached input, $4.25 output**.
Source: <https://dev.meta.ai/docs/pricing-rate-limits> (read 2026-10-27). Reasoning tokens are part of
`completion_tokens` and billed as output. A model that is not in the price table is priced at the
highest published rate ($4 in / $20 out) and the ledger entry says `price_assumed`.

## The guard

The ledger is one JSON file outside the repo: `~/.wattnot/meta_spend.json` (override with
`WATTNOT_META_LEDGER`). Per call:

1. estimate the worst case: request size at 3 chars/token, no cache discount, plus the full output cap;
2. under a file lock, **refuse** if recorded total + estimate > $50, **warn** above $40;
3. write a `pending` entry at the worst-case cost *before* sending, so a crash or timeout never hides spend;
4. settle it with the real usage from the response (time, model, input/output/cached/reasoning tokens,
   USD, purpose, latency). A rejected request is settled at $0; an unknown outcome (timeout) keeps the worst case.

`SparkClient` (planner, judge gate, Optuna warm start) uses the same guard, so a refused call raises and the caller's
normal fallback takes over. The key comes from `META_API_KEY`, or the Muse login file; it is never logged,
written to the ledger or sent anywhere except the API. Only WattNot telemetry, fixtures and public
prompts are sent. No schema ingest, no private text, no safety-test prompts.

Usage recorded in the repo before the ledger existed (planner fixtures, workload generation) is counted once
as `earlier:*`. Spend made by other tools on the same account (Muse Code, a dashboard) is not visible to the ledger.

## Commands

```bash
wattnot meta spend                 # total, per-purpose breakdown, remaining of $50 (--json for machines)
wattnot meta check                 # one tiny call to confirm muse-spark-1.3 answers
wattnot meta session --full-ladder --repeats 5 --cache-dir ~/.wattnot/meta_sessions/cache \
    --out session.json             # the real session below
```

## The 2026-10-27 session

Recorded in [meta-session-2026-10-27.json](../meta-session-2026-10-27.json).

| Step | What Muse Spark did | Data |
|---|---|---|
| next-knob | proposed the next setting for three toy surfaces (GPU power cap and vLLM knobs, MoE expert budget, hosted effort); every answer is checked against an allow-list | SIMULATED surfaces, real decisions |
| effort ladder | ordered the reasoning-effort levels per prompt class; we ran `workloads/cloud-effort-v1.jsonl` (15 checked prompts x 5 repeats) at every level and counted regex-check passes | live, MEASURED tokens and USD |
| coding set | answered the 48 public Muse Code prompts at `high`, `medium`, `low`; Muse Spark judged each against `high` through the judge gate | live |
| judge calibration | judged the public `data/judge-30.jsonl` against its human labels | live |
| receipts | explained the fixture receipts in plain words and listed the ESTIMATE numbers | fixtures |

Findings (small sets, one run; treat as a first measurement, not a benchmark):

- On the 15 checked prompts every effort level, `minimal` included, passed every regex check (100%). `minimal`
  used 70-80% fewer output tokens than `high`. The agent's order (cheapest-likely-first) saved 48.9% of cost vs
  `high`; running `minimal` everywhere would have saved 71.8%. The agent started the harder classes at
  `low`/`medium` and never tried `minimal` there, because it stops at the first pass.
- On the coding set the judge found 0 of 48 `low` answers and 0 of 47 `medium` answers worse than `high`;
  `low` cost 30.7% less, `medium` about the same as `high` on these short prompts.
- The judge agreed with the human labels on 30 of 30 items at `low` and at `medium` effort.
- The first coding-judge attempt ran out of output tokens while reasoning and failed closed; the judge call now
  allows 8000 tokens. That spend is in the ledger too.
