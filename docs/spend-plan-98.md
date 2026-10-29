# $98 plans for the hosted (Baseten) and Modal credits, plus the spend guard

Status: PLAN. Nothing paid has run. All $ are list-price ESTIMATES until reconciled with the bill.
Prices read 29 Oct 2026: hosted Spark (`meta-models/muse-spark-open`) $1.25 / $4.25 per 1M in/out;
Modal (`modal billing rates`): B200 $6.25/h, H100 $3.95/h, T4 $0.59/h, CPU $0.0473/core-h, mem $0.008/GiB-h.

## Guard (src/wattnot/spend)
- One append-only, fsynced JSONL ledger per provider (file-locked). Resume re-reads it; an unsettled
  reservation from a crash counts in full.
- Before every call / timed tick: reserve the worst case. Refuse if it would pass hard cap minus a
  $2 billing-lag margin (effective stop $96). Soft cap $90: the current item/block finishes, nothing new starts.
- Settle with actual token counts x price (hosted) or GPU-seconds x all-in rate (Modal).
- Reconcile with provider numbers (Modal `modal billing report/summary`, free); the provider number can
  only raise the ledger. Hosted: no usage API reachable with the inference key, so ledger + dashboard check.
- 402 / out of credit -> hard stop, sticky across resumes. 429 -> 3 backoffs then soft stop. 5xx /
  preemption -> soft stop (finished arms stay checkpointed). Failed calls are billed at worst case.
- One finish path: write results + receipt, copy them out, tear down cloud apps, list orphans.
- Modal backstop: `cloud_timeout_s()` sets the remote function's own timeout from headroom, so the cap
  holds even if the local watchdog dies. Also set a Modal workspace budget if the plan allows it.

## Hosted Spark, $98 (lands ~$85-88, soft $90)
Self-sizing: a calibration pass measures real tokens per item per arm, then repeats are set to land at ~$86.
Rough per-call at ~400 in / ~2,000 out: ~$0.009 -> one 92-item pass ~ $0.85.
| Item | Calls (est.) | $ est. |
|---|---|---|
| Calibration: 92 tiers x 1 | 92 | ~1 |
| Hosted baseline: 92 x 10 repeats (spread, CI) | 920 | ~8 |
| Reasoning-effort sweep low/med/high x 10 (if the API honours it; else max_tokens) | 2,760 | ~25 |
| Thinking-budget mirror of Glimmer: per-tier 0.5x/1x/2x budget x 10 | 2,760 | ~22 |
| Spark-as-judge over the H100 long-run answers (~5k answers, ~600 in / 250 out) | ~5,000 | ~9 |
| Agent-waste A/B at scale (with vs without WattNot) | ~1,000 | ~5 |
| Fill-up repeats of the cheapest-informative arm, to ~$86 | auto | ~15 |
| **Total** | ~12,500 | **~85** |
Story: hosted $/correct and tokens/correct vs H100 Glimmer J/correct and $/correct; budget/effort lever
measured on a second Meta model.
Dedicated deployment on the same platform: possible in principle (our own vLLM could take a top-k override),
but Spark NVFP4 needs 8x B200 (~$80/h list) plus a 533 GB load, and no power caps/NVML control. Under $98
that is <1 h with load risk, and duplicates Modal. Not recommended.

## Modal, $98 (planned ~$88, soft $90, effective hard $96)
8x B200 all-in ~ $55.6/h (GPUs $50 + 32 cores + 512 GiB).
| Step | Min | $ est. |
|---|---|---|
| Image build + 533 GB prefetch (CPU) + power probe (T4) + smoke (L4) | - | ~0.5 |
| 8x B200: load 12, warmup 3 | 15 | 13.9 |
| top-k 8 / 6 / 4, 18 min each + 2 restarts x 5 | 64 | 59.3 |
| top-k 8 repeat (noise floor) | 10 | 9.3 |
| receipt + exit | 2 | 1.9 |
| Glimmer FP8 1x H100, 40 min (cross-cloud reproduction of one J/correct number) | 40 | 3.2 |
| **Total** | | **~88** |
Branch: if k=6 fails the gate badly, skip k=4 and move ~20 min into k=8/k=6 repeats.
Volume storage: 533 GB is under the 1 TiB/month free allowance.

## Hosted Spark reasoning controls (docs, 29 Oct)
- `reasoning_effort`: `low` (strength 8), `medium` (32), `high` (256), `xhigh` (512, default). `none`/`minimal`
  run at `low`; reasoning can't be turned off. Sources: docs.baseten.co/inference/model-apis/reasoning,
  dev.meta.ai/docs/muse-spark-open/settings.
- `max_tokens` caps reasoning + answer together (a hard budget; too tight truncates -> gate counts a fail).
- `reasoning.max_tokens` (reasoning-only budget) is enforced on one other hosted model only, not Spark.
- Open-weight template: a system line `Reasoning strength: N` overrides `reasoning_effort` (any N), which
  would give a fine-grained budget; whether the hosted endpoint honours it is UNVERIFIED.
- Plan: effort sweep low/medium/high (+xhigh baseline) instead of 0.5x/1x/2x; max_tokens as the hard ceiling.

## Quality judge -> Muse Spark (hosted) by default, Llama 3.1 8B fallback
Today: `src/wattnot/gates/judge.py` defaults `JUDGE_MODEL` to Llama 3.1 8B (local vLLM), with Spark via
the Meta API only when no local URL. Switch: default transport = hosted Spark via the existing
`proof/baseten_planner.py` client (reasoning_effort=low, temperature 0, guarded by this ledger); on
401/402/429-exhausted/soft-or-hard stop, fall back to `LocalJudge` (Llama). Every verdict records `judge_model`.
Text to update: web/dash/metatools.js (lines 2, 42-45, 176, 317), web/dash/v2.js:1219,
web/dash/sample-meta5-receipt.json:129, src/wattnot/receipt/build.py:160, src/wattnot/dash/judgepath.py:75,
data/judge/judge-path.json:21, src/wattnot/rehearse.py:225, src/wattnot/dash/leaderboard.py:167,
README.md:51,56, docs/meta-tools-v5.md:6,109-110, docs/joules-per-correct-answer.md:36 (era note stays).
Judge cost: per-answer (~600 in / ~250 out at low effort, ~$0.0018) x ~5,000 long-run answers ~ $9;
batched 92-per-call ~ $2. Budgeted $9 above.

## Live proof, 29 Oct 2026 (approved by Akash 9:16 AM IST)
Key + model: inference works with `BASETEN_METRICS_KEY` (`BASETEN_API_KEY` returns 401). Code now reads
`BASETEN_METRICS_KEY` first, `BASETEN_API_KEY` as fallback (`wattnot.cloud.inference_key`). Model id is
`meta-models/muse-spark-open`; the old `-1026` slug is gone from live code (kept only in historical run files).

Cut-off rule: with `max_tokens` 64 the model spent 61 tokens reasoning and returned `content: None`. A reply with
no content is a **cut-off**: `FallbackJudge` treats it like a failed primary call and falls back to Llama, and if
there is no fallback the gate fails closed — never a pass. Judge budget is pinned to one value,
`JUDGE_MAX_TOKENS = 1500` at low effort, across the batch judge, hosted-Spark pricing and `record_judge`; 512
still spent the whole budget on reasoning for some replies.

### Baseten: 3 calls through the guard (low effort, max_tokens 256) - MEASURED
| | requests | input | cached input | output | $ |
|---|---|---|---|---|---|
| Guard ledger | 3 | 464 | (not discounted) | 161 | 0.001264 |
| Baseten usage API (`/v1/model_apis/usage`, 03:57Z bucket) | 3 | 464 | 384 | 161 | 0.000842 at list price incl. $0.15/M cached |

Tokens match exactly. The ledger is ~50% high because it ignores the cached-input discount: safe direction.
Answers: "56", "Paris", "Yes." (all finish=stop; 12/21/94 reasoning tokens). The usage API is reachable with the
metrics key (HTTP 200); rows appeared within ~5 min (empty right after the calls). No dashboard login was used.

### Modal: one 30 s CPU function (1 core, 1 GiB, ephemeral app) - MEASURED
| | seconds | $ |
|---|---|---|
| Guard ledger (whole local wall time: boot + run + teardown) | 44.0 | 0.000676 |
| `modal billing report --for today -r h --show-resources` (~5 min later) | - | 0.000492 (CPU 0.000421 + mem 0.000071) |

Ledger is above the bill: safe direction. `modal billing summary` rounds to cents, so use `report --json` for reconcile.
Teardown: app `ap-rXeXuNavaVH7UajTOTHPcX` state `stopped` at 09:32:32 IST, 0 tasks; guard found no orphans.

First attempt failed (container could not import `wattnot`, crash-looped ~200 s). The guard billed its worst case
($0.001843) on the exception and the app was stopped by Ctrl-C (`ap-3WVjua6Diiw7YBaEhL83Id`, stopped 09:31:31 IST);
Modal's report shows no cost for it. Lessons: (1) never import local packages at module top in Modal files;
(2) a reservation is not a timeout - every Modal block must also pass `cloud_timeout_s()` as the function `timeout`
and run under a local wall-clock kill, which the sprint scripts must keep.

Total real spend for the proof: about $0.0017 (Baseten ~$0.0012 incl. the first 64-token call, Modal ~$0.0005).
