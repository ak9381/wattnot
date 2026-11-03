# H100 16-hour run plan (Glimmer 30B, `--plan max`)

Written 3 Nov 2026, ~2:40 AM IST. **Plan only: nothing is launched.** Needs Akash's go and, very
likely, a credit top-up first (see Money). Labels: MEASURED (read from API/logs), ESTIMATE (computed).

## Why this plan
The `max` plan in `src/wattnot/longrun/driver.py` (`plan_glimmer_max`) is the one that covers every
path we want on real H100 power: text (sweep, tiers, thinking budget), eye, hearing (spoken
answers), LangChain through the waste proxy, the agent-waste saver, prefix cache, FP8, batching,
serverless idle, public benchmarks, and the run matrix. It sums to **15.62 h** of blocks
(ESTIMATE, from the driver's own block hours). It runs three passes (p1, p2, p3) of the core
blocks with different cap orders and data sets, so every headline number has n=3 across passes.

## Run order (block id, hours ESTIMATE)
| # | Block | h | What it proves |
|---|---|---|---|
| 1 | t0-setup | 0.57 | VM up, Glimmer weights, BF16 serve |
| 2 | t0-probe | 0.13 | **go/no-go**: power-cap write + readback, image acceptance |
| 3 | t1-sweep (p1) | 1.58 | caps 375/350/325/300 W, Wonder planner, gates, approvals, SAM, signed receipt |
| 4 | p1-think, p1-tiers, p1-eye, p1-spoken, p1-phase | 0.85 | text tiers, thinking budget, **eye**, **hearing**, phase clock |
| 5 | p1-langchain, p1-agentwaste | 0.75 | **LangChain** saver OFF/ON, **agent-waste saver** |
| 6 | p2-* (sweep 300->375, gsm8k300) | 3.18 | repeat pass 2 |
| 7 | p3-* (360/340/320/300, humaneval164) | 3.18 | repeat pass 3 |
| 8 | t2g-serverless | 0.5 | idle W, cold start J |
| 9 | t2-fp8, t2-eye, t2-spoken | 0.43 | FP8 must still see and hear or roll back |
| 10 | t2b-batch | 0.33 | max-num-seqs x kv-dtype |
| 11 | t2c-prefix | 0.67 | **prefix cache** off vs on, 3 reps |
| 12 | t2d-combined | 0.42 | best cap + prefix ON vs stock |
| 13 | t2e-public | 1.33 | GSM8K 300 + HumanEval 164, 3 reps |
| 14 | m3-h100, m4-combined | 1.6 | run-matrix legs on H100 |
| 15 | t3-copy | 0.08 | copy results back, then **delete the VM** |

Drop order if late (driver trims from the bottom; never drop 1-5): m4, m3, t2e trims, t2g, p3.

## Money (all ESTIMATE unless marked)
- Rate ₹451.2/h (MEASURED price incl. GST + 500 GB disk). 15.62 h ≈ **₹7,048**; with a 0.5 h launch
  margin ≈ ₹7,274.
- Last known balance **₹8,801.29** (MEASURED, 2 Nov). After the run ≈ ₹1,527, just above the
  **₹1,500 floor** the balance watchdog enforces. No slack for reruns.
- **E2E's 24 h launch rule needs ≥ ₹9,097 at launch.** ₹8,801 is ₹296 short, so the launch is
  refused without a top-up. A top-up of ≥ ₹1,500 is recommended (covers the rule and one rerun hour).
- Checkpoints (read the balance with the free read-only E2E call `ctl.py`'s watchdog uses; there is no `balance` subcommand): at each hour mark expect
  balance ≈ start − 451×h. Stop and investigate if it drifts > ₹100 from that.
  Hard stops: `RUN3_MAX_H=16.5`, `RUN3_BUDGET_CAP_INR=7500` (default is 2000, which would stop the run after ~4.4 h), `ctl.py watchdog` deletes below ₹1,500.
- `ctl.py launch` itself refuses unless balance ≥ max(16.5 h × list price, ₹9,097) and `RUN3_GO=1`.

## Break / fix / rerun loop
1. Every block writes a checkpoint and a receipt; the driver resumes after a lost link (exit 4 = state unknown, never "failed").
2. A failed block: read its `logs/<block>.log`, fix on a branch on the box (CPU tests + `--dry-run` of that block), push, `git pull` on the VM, rerun only that block.
3. **Same failure twice: stop that block**, mark it skipped with the cause, move on. Never loop.
4. After each pass (p1, p2, p3): rsync results to the box, commit to a `data/h100-1103` branch, push. Nothing is lost if the VM dies.
5. Spare time at the end: rerun skipped blocks first, then extra t2c-prefix reps. Delete the VM the moment t3-copy finishes.

## Pre-launch dry runs (3 Nov, CPU + mocks, no spend)
See "Dry-run results" below (filled from `python -m wattnot.longrun.driver --dry-run`).

## Dry-run results
Run 3 Nov 2026, 04:05-04:45 IST, on main f782d8a with the box CPU only. Every number is MOCK: no GPU, no network, no spend, and provider keys were removed from the environment.
Driver command: `PYTHONPATH=src python -m wattnot.longrun.driver --dry-run --out DIR --model M --plan P`. The driver uses `glimmer-test` for glimmer.

| Dry run | Result | Notes |
|---|---|---|
| glimmer `--plan max` (recommended) | PASS | 37/37 blocks done, 0 failures, about 24 min wall time |
| glimmer `--plan public` | PASS | 17/17 blocks done |
| glimmer `--plan long` | PASS | 16/16 blocks done |
| glimmer `--plan full` | PASS | 11 done; `g8-buffer` dropped by design (needs 0.75 h, 0.31 h left) |
| llama `--plan full` | PASS | 8 done; `b5-second-model` skipped by design (needs Akash's yes) |
| llama `--plan max` | refused (expected) | the guard says `--plan max needs --model glimmer-test` |
| `scripts/e2e/baseten_round.py --round r01 --mode smoke --limit 2 --dry-run` | PASS | status dry-run, 0 errors, billing not read |
| `scripts/e2e/round.py --dry-run` for modal, baseten and h100 | PASS | all rc 0 |
| `modal/spark_topk_sprint.py --dry-run` | PASS after a fix | Bug: it appended $0 "reconcile" rows to the tracked `data/spend/modal.ledger.jsonl`. Fixed in fix/dry-run-ledger: the dry run now writes `DIR/dry-run.ledger.jsonl`, with a regression test. |
| `scripts/e2e/preflight.py --no-dash` for baseten, h100, modal and combined | PASS | all rc 0 (launch allowed) |
| Prefix cache auto/on/off (`context_saver.prefix_switch.choose`) | PASS | auto turns it on for a shared prompt prefix (87%) and off for unique prompts (0%); on and off force the vLLM flag either way |
| `wattnot report eu --receipts receipts/rerun-1031/...cap325.json` | PASS | CSV and HTML written |
| `wattnot verify` on the H100 rerun receipt and the modal-r1/r2 energy receipts | PASS | every check ok |
| Round receipts of all 4 replays (signature check) | PASS | `check_receipt_file` is signed=True. Since 3 Nov (branch `fix/verify-round-receipts`) `wattnot verify` also reads round receipts: schema, sha256 and signature ok, signer flagged as a per-run key, grade ESTIMATE, exit 0. |

The mock tier gates show "blocked: p99 +12%". That comes from the synthetic latency model, not a real measurement.

## Open questions for Akash
1. Go/no-go for ~₹7,300 of H100 time, and will you top up (≥ ₹1,500) so E2E's 24 h launch rule passes?
2. Start time: the run needs ~16.5 h wall clock; to finish before the Thu 5 Nov 7:30 AM IST freeze with
   time to edit the video, it must start by about Tue 3 Nov evening IST at the latest.
3. Keep `max` (3 passes) or use `public` (6.2 h, ~₹2,800) if budget or time is tight?
4. Is it okay to spend cents of Meta API (Wonder planner, Spark judge) during the run (cap $100 hard / $80 soft)?
5. Should the camera-ready one-take recording happen on this VM (adds ~1 h) or do we use replays for the video?
