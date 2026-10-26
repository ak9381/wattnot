# M01 PRD: probe.py and host capability check
Depends on: none · Estimate: 3 h · Contracts: `schemas/SCHEMAS.md`

## Why
Before any run, find out what this host lets us do: read power, write power caps, lock clocks. Every later step picks container mode or root mode from this answer, so we never promise a knob the host blocks.

## Requirements
- FR-PROBE-01 Detect GPU(s), driver, CUDA, vLLM version, provider (Modal, Baseten, E2E, Jarvislabs, local).
- FR-PROBE-02 Test NVML reads: power, total energy counter, clocks, temp. Record sample rate achieved.
- FR-PROBE-03 Test power-cap write (`nvmlDeviceSetPowerManagementLimit`) inside the allowed range, then restore. Never leave a changed cap behind.
- FR-PROBE-04 Test clock lock (`nvidia-smi -lgc` / `nvmlDeviceSetGpuLockedClocks`), then reset.
- FR-PROBE-05 Output `capabilities.json` and emit it in `run.started.data.capabilities`. Decide `mode`: root if cap write works, else container.
- FR-PROBE-06 Finish in under 10 minutes so a bad rental can be released fast (about ₹380 on E2E hourly billing).

## Out of scope
Benchmarks, any tuning, any Meta API call.

## Done means
Runs on Modal and the rental; produces valid JSON; never leaves cap or clocks changed (checked by reading them back); runtime < 10 min.

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
