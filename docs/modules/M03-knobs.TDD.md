# M03 TDD: vLLM knob runner (container mode on Modal/Baseten)
Branch: `feat/m03-knobs` · Hours: 8 · Depends on: M02, M09 schema

## Interfaces
```python
@dataclass class TrialParams: max_num_seqs:int; enable_chunked_prefill:bool; enable_prefix_caching:bool; block_size:int; quantization:str; gpu_mem_util:float; power_cap_w:int|None=None; sm_clock_mhz:int|None=None
def run_trial(params: TrialParams, workload: Workload, meter: Meter, reps:int=5, emit:Emitter) -> TrialResult
```
CLI: `wattnot run --host modal --model meta-models/muse-spark-open-1026 --trials plan.json --reps 5`
Modal app: `deploy/modal_app.py` (one GPU function, vLLM server inside); Baseten: OpenAI-compatible `https://inference.baseten.co/v1` for hosted calls (no NVML there, so ESTIMATE only).

## Data schemas
Uses the canonical run event (`schemas/run-event.schema.json`) and receipt (`schemas/receipt.schema.json`). Module-local formats are defined under Interfaces. Never add fields to the canonical schemas inside this module; open a schema task instead.

## File layout (this module owns only these paths)
`wattnot/runner/{vllm_server,trial,workload}.py`, `deploy/modal_app.py`, `workloads/replay-v1.jsonl`, `workloads/muse-code-v1.jsonl`, `tests/test_runner.py`

## Test plan (tests first; write, watch fail, then pass)
Offline: a fake OpenAI-compatible server with scripted latencies; checks warm-up discard, 5 reps, percentile math, event order. Live smoke (manual, needs maintainer okay for GPU minutes): 1 rep baseline vs fp8 on Modal.

## Acceptance criteria
Baseline vs FP8 (or batching) with 5 vs 5 reps on Modal produces two TrialResults and a CSV per rep; the saving receipt goes through M6 and M7 by Oct 29.
- `make test` green, `ruff`/`tsc` clean, and the branch pushed.

## Dependencies
M02, M09 schema

Rules: no Meta API credits for building (recorded fixtures in tests); nothing confidential to the Meta API; ≤ 2 demo servers, 1 heavy job; push after the milestone.
