# Using WattNot behind an agent framework

WattNot's proxy speaks the OpenAI chat-completions API. Any framework that lets you set a base URL can use it without other code changes. You point the base URL at WattNot, and every call goes through the same layers:

1. **Agent-waste saver** stops loops that bring in no new information (repeated tool calls, no-op steps, cycles).
2. **Context saver** replaces repeated tool output with a short reference and folds old turns into a summary. A stage that would make the prompt bigger is skipped.
3. **Small model first**: Llama 3.1 8B answers first. If the reference-free answer gate fails, the call goes to Muse Glimmer 30B, then to Muse Spark Open.
4. **Quality gate** checks the final answer.

`GET /v1/wattnot/receipt` returns what happened: the framework name, calls, escalation rate, small-model pass rate, gate pass %, tokens before and after the context saver, and the waste counters.

## Examples

Each example changes only the base URL (plus an optional `X-WattNot-Framework` header, so the receipt names the framework; without it we guess from the User-Agent).

| Framework | Example | What changes |
|---|---|---|
| openai-python | `examples/frameworks/openai_python/agent.py` | `OpenAI(base_url=...)` |
| LangChain | `examples/frameworks/langchain/agent.py` | `ChatOpenAI(base_url=...)` |
| LlamaIndex | `examples/frameworks/llamaindex/agent.py` | `OpenAILike(api_base=...)` |

CrewAI isn't covered yet. It routes calls through its own LLM wrapper, and its install is heavy, so we kept the pinned extra small.

## Run the tests

```bash
pip install -e '.[frameworks,dev]'
pytest tests/test_frameworks.py
```

The tests start the proxy on a free local port with a mock model upstream, so they need no GPU and no network.

## Results (MOCK)

All numbers here are **MOCK**: the upstream is a scripted stand-in, not a real model.

| Check | openai-python | LangChain | LlamaIndex |
|---|---|---|---|
| Drop-in by base URL only | pass | pass | pass |
| Receipt names the framework | pass | pass | pass |
| Escalation rate on 1 easy + 1 hard prompt | 0.5 | 0.5 | 0.5 |
| Small-model pass rate | 0.5 | 0.5 | 0.5 |
| Gate pass % | 100 | 100 | 100 |

The loop test (openai-python) repeats one file read 12 times. The waste saver stops the run early, and the context saver cuts the repeated output (tokens are an ESTIMATE, about 4 characters per token). Real numbers on Muse need a live run and will be labelled MEASURED when we have them.

## Live LangChain run (MEASURED, 2026-11-01)

`tests/integration/test_langchain_real.py` drives real `langchain-openai` `ChatOpenAI` (including `.stream()`) and a `langchain.agents.create_agent` tool agent through the proxy to the live Meta Muse API (tiers: `muse-spark-1.3-contributor` first, then `muse-spark-1.3`, both marked `reasoning`). It is skipped unless `WATTNOT_LIVE=1` and a Meta key are set:

```bash
pip install -e '.[frameworks,dev]' langchain
WATTNOT_LIVE=1 python -m pytest tests/integration/test_langchain_real.py -s
```

Latest run (MEASURED, after the proxy fixes below): 8/8 correct (6 Q&A, 1 streamed answer in 4 chunks, 1 multiply-tool agent), 9 proxied calls, receipt framework `langchain`, small-tier pass rate 1.0, escalation rate 0.0, gate pass 100%, 9 billed upstream calls, about $0.0008 (ESTIMATE, spend ledger at list prices). A replayed tool loop was stopped early by the waste guard. Output: `receipts/frameworks/langchain-live.json`.

The first run (before the fixes) escalated 2 of 8 calls only because reasoning used up a 400-token output cap, and its waste stats showed 8 calls when 10 were billed.

## Proxy behaviour for frameworks

- **Reasoning tiers:** a tier with `"reasoning": True` gets `max_completion_tokens` of at least `max_output` (default 2000). A `finish_reason: length` with a visible answer, where reasoning used at least half the output, is gated as a normal answer. Only an empty or failing answer escalates.
- **Billed calls:** every upstream attempt, including escalated ones the caller never sees, is counted in the waste stats. The receipt shows `billed_upstream_calls`.
- **Streaming:** `stream: true` is served as OpenAI SSE chunks (with a usage chunk when `stream_options.include_usage` is set). The proxy buffers the upstream answer first, because the quality gate and routing need the full answer, so the first token arrives when the answer is done.
- **Fields:** only standard chat fields are forwarded (`model`, `messages`, `tools`, `tool_choice`, `temperature`, `top_p`, `max_tokens`, `max_completion_tokens`, `stop`, `response_format`, `seed`, penalties, `reasoning_effort`, `user`, `metadata`). Others are dropped and listed in the receipt's `dropped_fields`. `n > 1` returns 400.
