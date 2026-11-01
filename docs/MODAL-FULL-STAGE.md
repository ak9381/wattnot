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
| Backend/effort router | ON | thinking off first, per-tier thinking budget on a failed check; one backend |
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

~75 min of A100 at ~$2.64/h (rate seen in the smoke) = about $3.3; hard container timeout 3 h = $7.50
worst case. 3 rounds about $10, worst $22.5, inside the $40 stage cap (the runner refuses a round
whose worst case would cross it). Meta: Spark grades about $0.85 a round worst case, plus cents for
the live eye/hearing/SAM/Wonder calls with `--live-meta`.

## Go commands (need Akash's go)

    WATTNOT_MODAL_GO=1 python scripts/e2e/modal_full_round.py --round r04 --live-meta
    # add --live-pr to open the Muse Code PR on ak9381/wattnot-demo-deploy (only that repo)
    modal app list   # teardown is checked automatically after each round
