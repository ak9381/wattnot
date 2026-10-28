# Context saver

Three levers that cut wasted tokens on the agent path without making the model worse. Each is
measured honestly: every number is MEASURED, ESTIMATE or MOCK, and every cut goes through the same
quality gate — if the pass rate drops by more than **one point**, the cut is rolled back.

## 1. Prefix caching (a vLLM serving lever)

Many agent requests share a long prefix: a fixed system prompt plus repo context, then a short
varying question. vLLM's `enable_prefix_caching` keeps that prefix's KV cache so it is not
recomputed on every call.

- **Workload:** `workloads/prefix-cache-v1.jsonl` — one identical ~470-token system+context prefix
  in front of 12 checkable questions.
- **Experiment:** `scripts/prefix_cache_ab.py` runs two arms, `off` and `on`, interleaved, n=3.
- **Measured:** joules per correct answer, prefix-cache hit rate (from vLLM's Prometheus metrics
  `vllm:prefix_cache_hits_total` / `vllm:prefix_cache_queries_total`, or the
  `vllm:gpu_prefix_cache_hit_rate` gauge), TTFT, p99, and the quality gate.
- **Gate:** the `on` arm is kept only if the pass rate holds (within 1 point) and p99 does not rise
  more than 10%.
- **Where it runs:** the block-9 buffer of the long run (`scripts/h100_run.sh --prefix-cache`), so
  it adds no hours or budget. On CPU it runs as a MOCK block in the RUN3 rehearsal.

Module: `wattnot.context_saver.prefix_cache` (pure; `mock_ab()` for CPU, `run_ab(trial)` for live).

## 2. Memory compression (agent-path middleware)

`MemoryCompressionMiddleware` summarises old agent turns with Llama 3.1 8B into a **chunk store**,
keeps the system prompt and the last few turns verbatim, then **retrieves** the chunks most
relevant to the latest question and stitches them back as one compact recap. The summariser is
injected, so tests use `MockSummarizer` (deterministic, offline) and a live run uses a Llama 8B
client — no network in tests.

## 3. Tool-output dedup (agent-path middleware)

`ToolDedupMiddleware` keeps the first copy of a tool result (and the first read of a given file),
then replaces later **identical** results — or repeated reads of the same file with the same
arguments — with a one-line reference (`[identical to earlier result r1; N chars omitted]`). The
model still sees that the call happened; it just doesn't pay for the bytes twice.

## How they sit on the agent-waste path

`ContextSaver` chains the middlewares (dedup first, then compression) and counts tokens before and
after each stage. `WasteProxy(upstream, context_saver=ContextSaver())` runs the chain on every
request it forwards and reports the saving under `stats()["context_saver"]`:

```python
from wattnot.agentwaste import WasteProxy, WasteConfig
from wattnot.context_saver import ContextSaver

proxy = WasteProxy(upstream, WasteConfig(), context_saver=ContextSaver())
```

## Labels

- Token counts cut by the middlewares are an **ESTIMATE** off-GPU (about 4 characters per token);
  live, the served token counts are **MEASURED** from the usage field.
- Prefix-cache hit rate, TTFT and p99 are **MEASURED** on the GPU (vLLM metrics); **MOCK** in the
  rehearsal.
- Energy is an **ESTIMATE** (tokens × joules-per-token) unless taken from the NVML counter.

## The receipt block

`saver_receipt_block(off, on)` writes a `wattnot.context-saver/1` block with tokens, joules, pass
rate and cache hit rate **before and after**, each with its own label, plus the gate verdict
(`kept` is false when the pass rate dropped more than one point). The quality gate itself is
`quality_gate(before_pass_pct, after_pass_pct, max_drop_pts=1.0)`.

## Tests

`tests/test_context_saver.py`, `tests/test_prefix_cache_ab.py`, `tests/test_waste_proxy_saver.py`,
and a MOCK block each in the RUN3 rehearsal (`tests/test_rehearse_run3.py`).
