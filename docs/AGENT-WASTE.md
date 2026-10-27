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

## Second layer: behind a harness that already has a stop rule

The first benchmark compared the saver with an endless loop. Real harnesses are not endless:
they have a stop rule and a step cap. So the fair question is whether the saver still adds
anything when those are on. This benchmark asks exactly that. The answer is narrow: it adds
something on the runs where the stop rule misses, and it is a tie where the rule works.

### The three harnesses (ours, not the real libraries)

These are minimal loops we wrote for the benchmark, in
`src/wattnot/agentwaste/bench/harness.py`. They copy a common *pattern*. They are **not**
LangChain, AutoGen, LangGraph, CrewAI or the OpenAI Agents SDK, and nothing here says how
those libraries behave.

| Name here | Pattern | Its own stop rule and cap |
|---|---|---|
| `react` | tool-calling loop that ends on a text-only reply | `max_iterations` 15 |
| `stop` | ends when the reply holds a stop marker (`TASK_DONE`), else "Continue." | marker rule (contains, strict line, or JSON `final_answer`), cap 10 rounds |
| `planner` | a planner that hands work to helper agents through a `delegate` tool | planner cap 10, each helper cap 6, not shared |

In the "harness + saver" arm each agent's client is wrapped in `WasteProxy` and the loop
reads the `wattnot` field of the reply (`done` or `stopped`). That is about three lines per
harness. The harness keeps its own stop rule and cap in both arms. A bench-only safety stop
of 60 model calls per run applies to both arms so nothing can run away.

### What was added to the saver

New detectors, all off by default so the first benchmark still reproduces:

- **error_storm**: the same tool fails (or comes back empty) N times in a row, with any
  arguments. A retry with a fresh request id does not hide it.
- **low_gain**: the last 5 calls added almost no new words.
- **paraphrase**: text-only replies that say the same thing again.
- **SharedBudget**: one call, token and helper-agent budget shared by a planner and all its
  helpers (12 calls, 60,000 tokens, 4 agents). Used only for the planner harness.

`WasteConfig.second_layer()` turns them on. The nudge text for these tells the agent to call
`done` with a one-line reason and say what blocked it, so a blocked run reports a block
instead of a fake success.

### The tasks

- **Normal tasks, 18 cases**: 6 ordinary tasks (work, early, no-work) on each of the three
  harnesses. The saver should change nothing here.
- **Injected misses, 12 cases**, 3 each. The fault is deterministic. What the model does
  inside it is real Muse Spark.
  - *fuzzy completion*: the prompt and the stop rule disagree (the prompt says "Finished",
    the loop waits for `TASK_DONE`; JSON `final_answer` wanted but plain words asked for; a
    bare marker wanted but marker plus summary asked for).
  - *tool retry storms*: a status call that always returns 500, a write that always returns
    503, a docs search that is always empty. Each error carries a new request id.
  - *no-progress loops*: search then summarise, polling a ticket that never moves, polling a
    file that never changes.
  - *sub-agent sprawl*: double-checking helpers, re-delegating a task that already failed,
    recursive splitting.

Each case ran 2 times per arm on `muse-spark-1.3`, effort low: 120 runs, no API errors.
Success is checked on the world state and what the agent wrote. Calls never enter the check.
A **miss** is a run that ended on the harness's own cap (or the bench safety stop, or a
helper hit its cap).

### Numbers

Calls, tokens, wall time, success and misses are **MEASURED**. Energy and dollars are
**ESTIMATE** (same rates as above: 0.1497 J per generated token from the RUN2 baseline, and
the published Muse Spark price). Per run, harness alone vs harness + saver.

| | Normal tasks (36 runs per arm) | Injected misses (24 runs per arm) |
|---|---|---|
| Calls per run | 3.33 → 3.31 (-0.8%) | 13.0 → 5.62 (-56.7%) |
| Tokens per run | 4,681 → 4,879 (+4.2%) | 28,965 → 9,492 (-67.2%) |
| Wall time per run, mean | 3.12 s → 4.05 s (+29.8%) | 18.8 s → 6.0 s (-68.0%) |
| Wall time per run, median | 2.76 s → 2.99 s | 12.9 s → 5.1 s |
| Energy per run (ESTIMATE) | 44.8 J → 47.8 J (+6.8%) | 285.8 J → 131.3 J (-54.1%) |
| Cost per run (ESTIMATE) | $0.00442 → $0.00437 (-1.3%) | $0.02234 → $0.00868 (-61.2%) |
| Tasks passed | 36/36 → 36/36 | 24/24 → 23/24 |
| Runs that ended on the harness's own cap | 0 → 0 | 19 → 0 |

By kind of miss (calls per run, harness alone → with saver; misses out of 6 runs):

| Kind | Calls | Tokens | Passed | Misses |
|---|---|---|---|---|
| fuzzy completion | 10.0 → 3.67 | -73.0% | 6/6 → 6/6 | 6 → 0 |
| tool retry storms | 15.0 → 4.33 | -77.8% | 6/6 → 6/6 | 6 → 0 |
| no-progress loops | 15.0 → 5.67 | -75.1% | 6/6 → 5/6 | 6 → 0 |
| sub-agent sprawl | 12.0 → 8.83 | -23.8% | 6/6 → 6/6 | 1 → 0 |

### What this does and does not show

- **Normal tasks are a tie.** Same calls, same success, tokens +4% (most likely the extra `done`
  tool the saver adds to every request, which costs input tokens; we did not isolate it), cost within 1.3%. The mean wall time is
  worse only because two planner runs hit API latency spikes of about 22 s with ordinary
  token counts; the median is within 0.25 s. That is network noise, not the saver, but we
  report the mean as measured.
- **Most of the gain comes from the `done` action, not from detection.** In the three
  fuzzy cases the saver made no nudges: the model simply used the `done` tool the saver
  offers, in a form the harness accepts, where the loop's own marker never arrived. In the
  retry and no-progress cases a detector (error_storm, repeat, low_gain) fired first. The
  card and `results.json` carry the per-case nudge counts.
- **The harness cap catches these runs eventually**, and in 19 of 19 misses the task output
  was still right when the cap hit. "Success" is therefore the same in both arms; what the
  saver removes is the work after the answer was already known.
- **The saver lost once.** In `noprog-3-file-poll`, rep 2 (the saver arm), the file never
  changed, the saver stopped the repeated reads, and the agent's last message did not say
  that the value was still 120. The check failed it. The original repeat detector stops with
  a plain "Stopped early" message that carries no report; that is a real weak spot.
- **Sub-agent sprawl is the weakest result.** In `sprawl-3-recursive-split` the saver arm
  used more calls (8.5 → 12.0) and more tokens than the harness alone. The saver stopped the
  first helper early with a "blocked" report; the planner took that as a reason to hand the
  work to more helpers, and the shared budget then cut the planner off. The harness alone
  let one helper finish its search and the planner simply wrote up "not found". Both arms
  passed, but the saver did not help here and cost more. In
  `sprawl-1-double-check` the harness did not miss (it ended on its own) and the saver still
  cut calls from 15 to 8. In `sprawl-2-redelegate-failure` the harness missed once and the
  saver arm made no nudge at all; the two runs just took different paths.
- The sprawl failures are only partly reproducible: 1 of 6 harness runs missed.

### The live-demo replays in the dashboard

The Agent waste card in the dashboard shows the tables above and replays three recorded
runs side by side, harness alone vs harness + saver: *The stop word never arrives*
(`fuzzy-1-prompt-drift`), *A tool keeps failing* (`retry-1-status-500`), and *Helpers retry
what already failed* (`sprawl-2-redelegate-failure`). We chose these three before the
final run. For each, we replay the first rep where the harness alone missed (otherwise rep
0); we did not pick by how well the saver did. Because of that rule the third replay shows
a run where the saver did nothing; the card says so on screen. The replay is a REPLAY of a
real recorded run, not a new call. Nothing is running when you press Play.

### Limits

- One model, one small synthetic world, 2 reps per case. The faults are injected on
  purpose, so the miss rate (19 of 24 harness runs) tells you about these cases, not about
  how often real agents miss.
- Our three loops are small copies of patterns. Real libraries have more stop options than
  we modelled.
- Temperature 0 does not make Muse Spark deterministic; the same case can take different
  paths on two runs.
- A 24-run pilot with an earlier detector window (3 calls instead of 5) came before the
  final run. It found two bugs (a shared budget wrongly applied to single-agent runs, and a
  low_gain check that needed a per-call window). Both were fixed and the configuration was
  then fixed before the final run. Pilot runs are not in these numbers. Pilot spend is in
  the total below.
- API spend for this benchmark, pilot included: $1.60 (MEASURED, from the spend ledger).
- Tests use fake clients; nothing in `tests/` calls the network. Rerun with
  `python scripts/agentwaste_v2_bench.py --reps 2` (needs a Muse login; `--summarize-only`
  rebuilds `results.json` from `runs.jsonl` with no API calls).
