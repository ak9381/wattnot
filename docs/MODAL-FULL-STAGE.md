# Modal stage, option B: full WattNot on our own vLLM (Glimmer 30B, A100-80GB)

Branch `data/modal-full-1101`. Runner: `scripts/e2e/modal_full_round.py` (local) ->
`modal/full_stage.py` (A100 container) -> `scripts/e2e/modal_full_inner.py` (in the container).
The with-WattNot arm uses the shared pipeline `wattnot.e2e.fullstack` (same wiring as the
Baseten stage: `RequestLevers` on every with-arm call, `post_round()` after the answers) plus
the GPU-side levers below. Feature table = `gpu_feature_table()`.

## Arms

- without WattNot: BF16, vLLM prefix cache off, thinking on with a 3072-token budget, concurrency 4.
- with WattNot: everything ON in the table. Request path: agent saver + waste guard -> thinking-budget
  enforcer (think within B, forced close, then answer) -> step up to full thinking only when the fixed
  check fails -> reasoning-leak strip. Server: prefix cache on, kept batch size, FP8 only if its gate keeps it.

## Features

| Feature | Status | Why / how |
|---|---|---|
| Reasoning-effort + cheapest-first routing (low, step up on a failed check) | ON | hosted API (per-token, no GPU access) |
| Thinking-budget enforcer (max_tokens per effort) | ON | hosted API (per-token, no GPU access) |
| Backend/effort router | ON | per-tier thinking budget, step up on a failed check (Glimmer has no thinking-off switch); one backend |
| Agent saver: tool-output dedup + memory compression | ON | hosted API (per-token, no GPU access) |
| Agent-waste proxy (repeat/no-op/cycle/overspend detectors) | ON | hosted API (per-token, no GPU access) |
| Code Mode with locked sandbox + network boundary | ON | runs on the box over recorded traces |
| Prefix caching (provider side, reported cached tokens) | ON | our own vLLM; cached_tokens MEASURED from usage |
| Concurrency batching (client side) | ON | server-side batching via the kept --max-num-seqs |
| Noise gate, fixed answer gate, LLM judge, reasoning-leak strip | ON | hosted API (per-token, no GPU access) |
| Muse Spark grades every answer | ON | Meta API |
| Joule-SLO + user p99 limit | ON | Joule-SLO + p99 limit on MEASURED energy |
| Eye check: vision gate on the SYNTHETIC Muse Image set | ON | Meta API |
| SAM 3.1 hotspot audit | ON | Meta API |
| Hearing check: Muse Voice Transcribe on spoken answers | ON | Meta API |
| Muse Wonder planner + Wonderchart | ON | Meta API |
| Spark 4-call trace (probe, proposal, gate, receipt) | ON | Meta API |
| Approve-before-apply with risk classifier, undo | ON | local |
| Muse Code PR flow (ak9381/wattnot-demo-deploy only) | ON | patch always built; PR opened only with --open-pr at stage time |
| Signed receipt + SCI carbon/water + joules per good answer | ON | local signing key |
| EU report | ON | local |
| Label-honesty lint | ON | local |
| Spend guards (Baseten hard cap, Meta soft cap, ledger) | ON | local |
| Power-cap sweep + J/correct gate | OFF | Modal refuses NVML power-limit writes (probed each round, recorded) |
| FP8 quantization keep/reject gate | ON | restart vLLM with --quantization fp8; kept only if accuracy holds (-2 pts) and J/correct drops, with the eye and hearing checks re-run on the candidate |
| Batch-size + KV-cache sweep | ON | sweep client concurrency x --max-num-seqs on a 24-item slice, keep the lowest MEASURED J/correct inside the quality gate |
| vLLM prefix-cache control | ON | --enable-prefix-caching in the with-WattNot server, off in the stock server |
| Phase-aware clock sidecar, marginal-joule knee | OFF | needs GPU clock writes, which Modal also refuses |
| MoE top-k / per-expert pinning | OFF | Glimmer 30B is served here; MoE top-k needs Spark Open self-served (8x B200) |
| MEASURED GPU energy (NVML) | ON | NVML total-energy counter brackets each arm and each chunk of items |
| Flex grid-event load shedding | OFF | simulator only |
| Rekor publish | OFF | only after the long run, with Akash's yes |

## Per-round steps (in the container)

probe (power-cap write test) -> without arm (192 items: ours 92, GSM8K 60, HumanEval 40) ->
batch/KV sweep (3 configs x 24-item slice) -> FP8 gate (+ eye and hearing re-check) -> with arm ->
p99 / Joule-SLO / quality gate -> agent-waste A/B, LangChain proxy A/B, eye check, hearing check,
thinking-budget block. Then locally: Spark grades all 384 answers, signed NVML receipt (test key),
`wattnot.flow --gpu-dir`, `post_round()` (SAM 3.1, Wonder planner + chart, Spark 4-call trace,
approvals, Muse Code patch, Code Mode, hosted-style receipt + SCI, EU report, label lint).

## Recorded per round (`data/modal-full-1101/<round>/`)

`inner.json` (every step), `raw/*.answers.jsonl` + `*.answers.graded.jsonl` (answers, checks, Spark
verdicts), `raw/*.calls.jsonl` (every call), `raw/*.power.csv` (1 Hz NVML watts), `raw/vllm-*.log`,
block folders, `sweep/rcpt_*.json` (+ sigstore bundle), `flow/`, `features/`, `round.json`;
`out/matrix/<round>.json` for the dashboard.

## Smoke tests (Nov 1, 1 item per set, levers partly skipped)

- smoke-r04 and smoke-r05: A100-SXM4-80GB, idle 65.3 W MEASURED, power-cap write refused
  (NVMLError_NoPermission) MEASURED, vLLM ready in 276 s cold / 102 s warm MEASURED.
- Glimmer ignores both `enable_thinking: false` and `reasoning_effort: low` (same reasoning tokens,
  MEASURED), so the with-arm now uses the two-phase thinking-budget enforcer instead. That path,
  FP8 on the A100, the batch sweep and the agent/LangChain/think/hearing blocks are tested on the fake
  server only, not yet on the GPU.
- Modal spend for both smokes: $0.73 MEASURED (`modal billing summary` metered $2.75 -> $3.48).

## Cost per full round (ESTIMATE)

~75 min of A100 at ~$2.64/h (rate seen in the smoke) = about $3.3; hard container timeout 4 h = $10 per round (raised from $7.50 on 1 Nov)
worst case. 3 rounds about $10, worst $22.5, inside the $90 stage cap (raised from $40 to $60 on 1 Nov, then to $90 on 3 Nov, 7:45 AM IST, with Akash's yes) (the runner refuses a round
whose worst case would cross it). Meta: Spark grades about $0.85 a round worst case, plus cents for
the live eye/hearing/SAM/Wonder calls with `--live-meta`.

## Go commands (need Akash's go)

    WATTNOT_MODAL_GO=1 python scripts/e2e/modal_full_round.py --round r04 --live-meta
    # add --live-pr to open the Muse Code PR on ak9381/wattnot-demo-deploy (only that repo)
    modal app list   # teardown is checked automatically after each round

## Pre-run review (Nov 1, 5:15-5:45 PM IST, no spend)

Own review plus two passes of a second-opinion code review on the diff. Fixed:

- Fail soft, never crash: BudgetChat (falls back to the plain budget, switched off after 5 errors),
  FP8 (any start/answer failure keeps BF16; with-arm server falls back to BF16/64 seqs), batch sweep
  (per config), every GPU block (agent waste, LangChain, eye, hearing, thinking), each item, Spark
  grading, the NVML receipt. Each failure is recorded and the feature reads FAILED in the table
  (`gpu_feature_table(failed)`); time-skips read "skipped", not FAILED. Fault-injection test:
  `tests/test_modal_full_stage.py::test_untested_paths_fail_soft` (WATTNOT_FAKE_FAIL=fp8,budget,eye).
- No lost results: the container runs the inner step with a deadline (optional steps skip near it)
  and a subprocess timeout well before the container timeout, truncates vLLM logs, then tars and
  returns; the os._exit backstop is the last resort only. Fails fast if the weights volume is empty.
  Server start waits are bounded by time left; GPU memory is checked free between restarts; NVML
  calls are serialised.
- Spend: the worst case is reserved in stage_spend.json BEFORE the GPU starts and reconciled after
  (a crash cannot hide a paid round); the cap counts spent + reservations; Modal's own metered
  `ephemeral_apps` delta (MEASURED) is recorded and trues up the estimate if higher; smoke rounds
  are limited to --limit 1..3; refuses to start if any wattnot-full-stage app is running or
  `modal app list` cannot be read.
- Teardown: retried up to 5 times, tolerant of the CLI's JSON shape, `verified_empty` recorded,
  loud alert otherwise; runs even when `modal run` times out or the CLI is missing.
- Known gaps closed: the post-round Muse Code change and the approval now use the real kept config
  (precision, --max-num-seqs, router, thinking budget, saving %), not a fixed batch of 16; the hosted
  receipt carries the stage's energy label (MEASURED on Modal); the unused musecode() helper is gone.
- Approvals: no auto-approve. `verify_change` checks accuracy vs baseline (-2 pts), p99 (+10%),
  Joule-SLO, Spark agreement (>= 90%), the FP8 gate, the receipt hash and no fatal error; the PR is
  opened only if all pass (`approval_evidence.json`). `scripts/e2e/modal_stage.py` reviews the PR
  (repo, base main, allowed files, no secrets, size, launch.sh and values.yaml parse on the PR head)
  into `pr_review/pr_review.json`, from a fresh clone (never the local demo2 working copy).
- Graph: the container streams events.jsonl live through a Modal Queue, split per arm
  (`<tag>.without.events.jsonl`, `<tag>.with.events.jsonl`). The without arm names baseline_nodes
  (workload, models, telemetry, gates) so every WattNot node is grey; a `stage.features` event and
  `features/stage_features.json` carry the 26 ON / 5 OFF table plus `off_nodes` (adaptive, flex,
  spark_open, baseten_model, meters_roadmap, certificate, e2e_rig) so the graph can grey them up front.
- Clips: `scripts/e2e/modal_stage.py` records graph-without, graph-with, the matrix page and the PR
  review per round (one dashboard server on :8772 at a time) and checks each clip plays (ffprobe).
  Run with WN_PY=/usr/bin/python3 (has Playwright).

Not run: no paid rounds and no new GPU smoke (Akash: runs will be launched from the Configure UI).
FP8 on the A100, the batch sweep, BudgetChat and the GPU blocks remain tested only on the fake server.
