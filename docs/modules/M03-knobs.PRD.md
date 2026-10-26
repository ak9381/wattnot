# M03 PRD: vLLM knob runner (container mode on Modal/Baseten)
Depends on: M02, M09 schema · Estimate: 8 h · Contracts: `schemas/SCHEMAS.md`

## Why
Run Muse Spark Open under vLLM with one setting changed, replay the public workload, and measure Wh, $, p50/p99, TTFT and ITL. This produces the safe headline: a container-mode FP8 or batching receipt by Oct 29.

## Requirements
- FR-KNOB-01 Knobs: `max_num_seqs`, `enable_chunked_prefill`, `enable_prefix_caching`, `block_size`, `quantization` (none/fp8/awq), `gpu_memory_utilization`.
- FR-KNOB-02 Each trial = restart vLLM with params, warm up (discard first 20 requests), then 5 reps of the replay set.
- FR-KNOB-03 Workload: public replay set + Muse Code task set v1 (scripted, generated once, stored). Never private prompts.
- FR-KNOB-04 Emit `trial.proposed`, `trial.measured` events; save raw per-request latencies.
- FR-KNOB-05 Modal first (container mode; power from NVML read if allowed, else label ESTIMATE). Baseten as second host.
- FR-KNOB-06 One heavy job at a time: a lock file `/tmp/wattnot-heavy.lock` blocks a second sweep or download.

## Out of scope
Power caps (M4). Picking which trial to run (M5). Deciding pass/fail (M6).

## Done means
Baseline vs FP8 (or batching) with 5 vs 5 reps on Modal produces two TrialResults and a CSV per rep; the saving receipt goes through M6 and M7 by Oct 29.

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
