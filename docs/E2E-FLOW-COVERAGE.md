# E2E flow coverage (1 Nov 2026)

One flow: `python -m wattnot.flow`. On the H100 it is driven per pass by
`python -m wattnot.longrun.driver --live --plan max` (the GPU layers), then
`scripts/e2e/post_run_offgpu.py OUT` runs the flow per pass with `--gpu-dir` (off-GPU layers +
one signed flow receipt per pass). Off-GPU today: `python -m wattnot.flow --out DIR` runs all 35
steps in ~16 s, 0 failed (tested; `tests/test_max_plan.py`).

Modes: **GPU live** = measured on the H100 in a pass (MEASURED). **Off-GPU live** = really runs
on the control box. **Replay** = recorded MEASURED data, labelled REPLAY. **Fake** = SIMULATED/MOCK
stand-in used only when the flow runs without a GPU pass. **Not in flow** = with the reason.

## Master table rows

| Feature | Flow step | Mode on the H100 run | Mode off-GPU now |
|---|---|---|---|
| Power-cap sweep + J/correct gate | gpu-sweep | GPU live | Replay (rerun-1031 receipt) |
| Simulated GPU power model | flex-shed (`--backend sim`) | Off-GPU live, SIMULATED | same |
| NVML capability probe | nvml-probe (t0-probe on VM) | GPU live | Off-GPU live (reports no GPU) |
| FP8 keep/reject gate | t2-fp8 + t2-eye/t2-spoken (dedicated, once) | GPU live | dry run SIMULATED |
| Thinking-budget enforcer | thinking-budget (pN-think) | GPU live | Fake SIMULATED |
| MoE adaptive top-k | not in flow | needs a B200-class box for Spark Open; Glimmer is dense | — |
| Flex load shedding | flex-shed | Off-GPU live, SIMULATED backend (a GPU shed would cap mid-pass and spoil the sweep) | same |
| Fleet impact estimator | fleet-impact | Off-GPU live, ESTIMATE from the pass's Wh delta | ESTIMATE from replay |
| Model fit / per-model J | gpu-sweep receipt | GPU live | Replay |
| Tool-output dedup | langchain (saver ON) | GPU live | MOCK upstream |
| Memory compression | langchain (saver ON) | GPU live | MOCK upstream |
| Prefix-cache A/B | t2c-prefix (dedicated, 5 reps) | GPU live | dry run |
| Code Mode + locked sandbox | codemode | Off-GPU live (sandbox), traces REPLAY | same |
| Effort / cheapest-first router | langchain (thinking off → on) + cloud-router | GPU live + Replay | MOCK + Replay |
| Noise gate | gpu-sweep (Welch + bootstrap in receipt) | GPU live | Replay |
| Fixed answer gate | gpu-sweep, tiers, langchain grading | GPU live | Replay/Fake |
| LLM judge gate | gpu-sweep (orchestrate) | GPU live (Meta call, cents) | Replay |
| Reasoning-leak strip | gpu-sweep client | GPU live | Replay |
| Label honesty lint | label-lint | Off-GPU live | same |
| Code-sandbox network boundary | codemode | Off-GPU live | same |
| Approve-before-apply + risk | gpu-sweep (`--act` + policy) and approvals (propose → approve → apply → rollback → audit verify, real local store) | GPU live + Off-GPU live | Off-GPU live |
| Wonder Pareto planner | gpu-sweep (`--wonder-live`) | GPU live (Meta call) | Replay |
| Wonderchart | wonderchart | Off-GPU live with `--live-meta` (paid, cents) | MOCK |
| SAM 3.1 hotspot | gpu-sweep (`--sam live`) + sam-hotspot | Off-GPU live with `--live-meta` | MOCK |
| Spark planner + effort router | gpu-sweep / cloud-router | Replay for the cloud router | Replay |
| Muse Spark Open (served MoE) | not in flow | needs B200; Baseten hosting is in the multi-provider step | — |
| Muse Glimmer 30B | every GPU layer | GPU live | Replay |
| Muse Code apply-as-PR | muse-code-pr | Replay (we publish nothing, open no PR) | Replay |
| Muse Voice Transcribe | spoken-gate | GPU live (Glimmer answers) | Fake |
| Voice guide | voice-tuner | Off-GPU live (local parse) | same |
| Tuner | voice-tuner | Off-GPU live (local parse; clamps 200–700 W) | same |
| Muse Image | not in flow | regional block | — |
| Signed receipt + canon | gpu-sweep + flow-receipt | GPU live + Off-GPU live | Replay + Off-GPU live |
| Independent verifier | verify, verify-standalone, flow-verify | Off-GPU live | same |
| Joules per good answer | points | Off-GPU live on pass data | Replay |
| EU report | eu-report | Off-GPU live | same on replay |
| Cloud effort-router receipt | cloud-router | Replay | Replay |
| Hosted A/B / multi-provider | multi-provider | plan + Replay; same-prompt paid run needs Akash's go (`--mp-results`) | Replay |
| Certificate | certificate | Off-GPU live, MOCK watermark | same |
| Recommend/health/runbook/rehearse/demo | preflight step (health); others not in flow | ops tools, not a measurement | — |
| Dashboard shell / views / report / EU card / voice UI / hotspot / wonderchart / cost / code-mode / meta-tools | dashboard (`dash/flow.json` + event log) | Off-GPU live; the UI reads the replay pack | same |
| Five-layer watchdog, heartbeat | driver + ctl.py on the live run | GPU live (ops) | not in the off-GPU flow (ops, tested in the dry run) |
| Spend guard, timeout backstop, Meta ledger | spend-guard | Off-GPU live, SIMULATED dry-run | same |
| Own rented GPU | driver | GPU live | — |
| Hosted-inference adapter (Baseten) | multi-provider | paid, needs go | Replay |
| Serverless-GPU adapter (Modal) | multi-provider + follow-up phase | paid, needs go | Replay |

## Nov 1 audit rows

| Row | Flow step | H100 run | Off-GPU now |
|---|---|---|---|
| A1 Voice Ask | voice-tuner (intent); live Ask uses Meta, not in flow | Off-GPU live | same |
| A2 Public benchmarks | t2e-public (GSM8K 300 + HumanEval 164) | GPU live | dry run |
| A3 Combined | t2d-combined | GPU live | dry run |
| A4 Every-cap grid | gpu-sweep, 3 cap orders across passes | GPU live | Replay |
| A5 Dashboard login | not in flow | UI gate; Akash's UI review | — |
| A6 Approvals + undo | approvals (real store, not MOCK) | Off-GPU live | same |
| A7 Approvals API | approvals (CLI over the same store) | Off-GPU live | same |
| A8 Receipts API | receipts-api | Off-GPU live | same |
| A9 Agent-waste proxy | langchain, agentwaste-gpu | GPU live | MOCK/SIMULATED |
| A10 Phase clock | phase-clock | GPU live (3 min) | Fake |
| A11 Optuna planner | optuna-planner | Off-GPU live, SYNTHETIC objective (a live Optuna loop would need its own GPU hours) | same |
| A12 Knee | knee | Off-GPU live on pass points | Replay |
| A13 Joule-SLO | joule-slo | Off-GPU live on pass points | Replay |
| A14 Router | cloud-router, multi-provider, langchain effort routing | Replay + GPU live | Replay + MOCK |
| A15 Eye test | eye-gate | GPU live | Fake |
| A16 Event log | dashboard | Off-GPU live | same |
| A17 MoE top-k (CPU) | not in flow | needs B200 for a measured saving | — |
| A18 Expert pinning | not in flow | SIMULATED only; needs Spark Open served | — |
| A19 Narrator | narrator | Replay (live narration is a Meta call; optional) | Replay |
| A20 Batch / KV sweep | t2b-batch | GPU live | dry run |
| A21 No-mock preflight | preflight + driver `--live` audit | Off-GPU live | same |
| A22 VM controller | ctl.py | ops, launch needs Akash's go | — |
| A23 Replay container/site | replay pack (post_run_offgpu) | Off-GPU live | — |
| A24 Licences | datasets pinned by sha256 | n/a | n/a |
| N1 Mocks removed from live | driver `--live` + nomock | yes | — |
| N2 Live Ask | voice-tuner | Off-GPU live | same |
| N3 LangChain proxy | langchain (every pass) | GPU live | MOCK |
| N4 Rerun receipt | the off-GPU replay source | Replay | Replay |
| N5 Licences | as A24 | n/a | n/a |
| N6 Mac dashboard + tunnel | not in flow | user-run | — |
| N7 Dataset picker | `--dataset` own / gsm8k300 / humaneval164 per pass | GPU live | Off-GPU live |
| N8 p99 setting | flow receipt `limits` block | Off-GPU live | same |
| N9 Long-run plan | superseded by `--plan max` | — | — |
| N10 Receipt converter | not in flow | packaging tool | — |
| N11 Watchdog vars | ops (set by hand before launch) | — | — |
| N12 init / preflight | preflight | Off-GPU live | same |

## Follow-up phase: leftover Modal / Baseten credit (needs Akash's go)

After the H100 passes and the multi-provider scenario, if credit is left, run the same flow again on that provider:

- **Modal (H100 available):** same `--plan max` pass layout (sweep, think, tiers, eye, spoken, phase, LangChain, agent-waste) on a Modal H100, then `python -m wattnot.flow --gpu-dir <pass> --dataset <set>` off-GPU, same signed flow receipt. Energy is MEASURED by NVML in the container. Limits: Modal containers may not allow power-cap or clock-lock changes; any layer that needs them is recorded as "not permitted on host", not faked.
- **Baseten:** hosted model only, no GPU counters and no knobs. It can rerun only the quality, cost and routing layers (LangChain agent, tiers, multi-provider). Energy is ESTIMATE.
- Spend estimate (ESTIMATE): Modal H100 about $3.95/h list price. One pass is about 3.2 h plus about 0.3 h cold start and model load, so about $14 per pass. Leftover credit is $100 minus the multi-provider scenario (about $2.33) minus any earlier spend, which must be read from the Modal dashboard before launch.
- Nothing here runs without Akash's go. Guard: per-pass `--cap-usd` and the existing spend guard.
