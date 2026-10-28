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
