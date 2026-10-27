# Agent waste saver

Agent loops waste energy in a boring way. The model finishes the job, but the harness never
lets it stop, so it keeps calling tools, re-checking state it already knows, or thinking hard
about nothing. Every extra call is real tokens, real GPU time and real money.

`wattnot.agentwaste` sits between an agent and any OpenAI-compatible chat endpoint and
gives the run a way to stop.

Every number below carries a label: **MEASURED** (counted from the API), **ESTIMATE**
(converted with a stated rate).

## What it does

It rebuilds the run from the `messages` list on each request (assistant `tool_calls` plus the
matching `tool` results), so it keeps no hidden state and works behind any client.

| Detector | Fires when |
|---|---|
| repeat | the same tool, same arguments and same result happened again in the last step |
| no-op | a result is byte-identical to an earlier one, says `changed: false` / "no change", or the model sends an idle text-only turn after it has already used tools |
| cycle | a block of 2 to 4 distinct calls repeats back to back |
| reasoning | the last step used more reasoning tokens than `max(400, 4x the median of earlier steps)`, or more than 3000 |

What it does about it:

1. First detection: adds a short nudge ("say done if the task is finished") to a copy of the
   request. Your stored history is untouched.
2. A fresh detection after the nudge: the run stops with a reason.
3. A `done` tool is added to the tool list. If the model calls it, the proxy returns a clean
   final answer and never forwards another upstream call.
4. Budgets: `max_steps` (12 upstream calls) and `max_total_tokens` (60000).
5. Reasoning overspend also lowers `reasoning_effort` one level and caps `max_tokens`.

A stop answers with a normal chat completion (`id: wattnot-stop`, `Stopped early: <reason>`)
and a `wattnot` key that says why. `GET /wattnot/stats` shows counters per run.

```python
from wattnot.agentwaste.server import create_app, http_upstream
app = create_app(http_upstream("https://your-endpoint/v1"))   # run with uvicorn
```

In process, wrap any callable with `WasteProxy(client, WasteConfig(...))`.

## Benchmark

24 small ops tasks in a deterministic fake world (tickets, files, services, docs; no network,
no randomness), run against the Muse Spark API (`muse-spark-1.3`, effort low, temperature 0):

- 8 **work** tasks that need real changes,
- 8 **early** tasks that are done after one or two calls,
- 8 **no_work** tasks where there is nothing to do and the right move is to say so.

Each task is checked against the final world state and what the agent said. Call counts are
never part of the check. Each task ran twice per arm: 144 runs.

Three arms:

- **loop-off**: an autonomous loop. If the model answers with text only, the harness says
  "Continue." The call cap is 12. This is the failure the saver targets.
- **loop-on**: the same loop behind the saver.
- **auto-ref**: a normal harness that ends on the first text-only reply, no saver. This is the
  honest reference. Most real agents already behave like this.

Results per run, 48 runs per arm (reproduce with `scripts/agentwaste_bench.py`; raw runs in
`data/agent-waste/runs.jsonl`, summary in `results.json`):

| Arm | Success (MEASURED) | Calls | Total tokens | Wall time | Est. energy | Est. cost |
|---|---|---|---|---|---|---|
| loop-off | 42 / 48 (87.5%) | 12.0 | 24,355 | 17.6 s | 341 J | $0.0202 |
| loop-on | 48 / 48 (100%) | 3.48 | 5,239 | 2.9 s | 55 J | $0.0039 |
| auto-ref | 48 / 48 (100%) | 3.44 | 4,975 | 2.8 s | 52 J | $0.0040 |

Calls, tokens, wall time and success are MEASURED. Energy and cost are ESTIMATE.

Saver on versus off: 71% fewer calls, 78% fewer tokens, 83% less wall time, 84% fewer
generated tokens, about 81% less cost. It holds in each group (tokens -73% on work, -85% on
early, -76% on no_work).

Success did not drop. It went up. In the six runs where success differed, loop-off kept
"working" after the job was done and changed things nobody asked for (claimed tickets, opened
a ticket, moved a status). The saver stopped those runs before they could.

Against the normal harness the saver is a tie: +1% calls, +5% tokens, same success. We
do not claim it beats a harness that already stops on its own. It gives a loop harness the
cost of one.

### How the numbers are converted (ESTIMATE)

- **Energy** = completion tokens x 0.1497 J/token. That rate is MEASURED in RUN2 for Llama 3.1
  8B on one H100 at the stock 700 W cap (`data/h100-run2/vm/baseline.json`). Muse Spark is a
  different, larger model on hardware we do not control, so this is a stand-in rate. Read it
  as a relative comparison between arms, not as Muse Spark's real draw. Only generated tokens
  are counted.
- **Cost** uses the published Muse Spark price ($1.25 per 1M input, $0.15 per 1M cached
  input, $4.25 per 1M output) on the billed tokens. Source:
  <https://dev.meta.ai/docs/pricing-rate-limits>.
- API spend for the whole benchmark, pilot included, was $1.48 (MEASURED, from the spend
  ledger).

### Limits, plainly

- One model, one small synthetic world, 2 repeats per task. It shows the mechanism works; it
  is not a general claim about all agents.
- Wall time came from 4 runs in parallel against a shared API, so read it as relative.
- Our tasks are short (a handful of real steps). A long task that truly needs dozens of steps
  would leave the saver less to cut, though the step and token budgets still apply.
- Five runs in the first pass hit an API timeout or the spend guard and were re-run; the
  summary has no errors. After the first pass we also widened the "nothing to do" wording
  check (it missed "no unowned..." and "no fix needed"). This applies to every arm, and
  `--rescore` redoes it from the saved replies with no API calls.
- Tests use a mocked client; nothing in `tests/` calls the network.
