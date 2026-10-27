# H100 RUN2 raw data (27 Oct 2026)

One NVIDIA H100 80GB HBM3 (E2E Networks, Delhi), Llama-3.1-8B-Instruct on vLLM, 111-prompt gate.
Times inside the files are UTC (IST is +5:30).

- `events/sweep.events.jsonl`: orchestrator event stream (room scan, baseline, quality gate, Muse
  proposals and the cap sweep up to the 450 W block). It stops there: the control machine's SSH
  session to the GPU host hung and the orchestrator was stopped. The host kept the full results.
- `events/adaptive.events.jsonl`: the adaptive top-k step, run on its own afterwards
  (`scripts/run2_adaptive_only.py`).
- `vm/`: everything the GPU host wrote: `baseline.json`, `control.json`, `steps/cap*.json`
  (per-cap trials, gates, `accepted`), `raw/` (per-request answers), `vm_events.jsonl`,
  `adaptive_topk/` (summary and the MoE receipt), `traces/` (NVML energy traces), command logs
  and the vLLM log.
- `gpu/`: 1 Hz `nvidia-smi` trace of the whole session (idle power, temperatures, throttle
  reasons), full and power queries, package versions, setup log.

Not in this data: the keep/roll-back step, the 300 W and 250 W grid caps, the signed run receipt,
and the flex shed test. They did not run.
